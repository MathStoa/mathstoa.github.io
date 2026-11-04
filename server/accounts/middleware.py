from django.conf import settings
from allauth.core import ratelimit
from allauth.socialaccount.adapter import get_adapter
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound
from django.utils.cache import add_never_cache_headers
import ipaddress
import unicodedata


def limit_action(request, *, action, key=None, user=None, retry_after=3600):
    """Use allauth's locked, shared cache; do not put identifiers in responses."""
    if ratelimit.consume(request, action=action, key=key, user=user):
        return None
    response = HttpResponse(
        "Too many requests. Please wait before trying again.",
        status=429, content_type="text/plain; charset=utf-8",
    )
    # A conservative bound for the action's longest configured window.
    # The response is never cacheable.
    response["Retry-After"] = str(retry_after)
    add_never_cache_headers(response)
    return response


def admin_network_key(request):
    # REMOTE_ADDR is the socket peer. Never trust a client-supplied forwarded IP.
    # Behind Render this may group several users under its proxy; the separate
    # account limit still applies across proxy peers. Keys are hashed by allauth.
    try:
        address = ipaddress.ip_address(request.META.get("REMOTE_ADDR", ""))
        if isinstance(address, ipaddress.IPv6Address):
            address = address.ipv4_mapped or ipaddress.ip_network(f"{address}/64", strict=False).network_address
        return f"admin-network:{address}"
    except ValueError:
        return "admin-network:unknown"


class AccountPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        from django.shortcuts import render
        from .organisation_login import organisation_account

        # allauth's generic login form can also initiate an email code. Keeping
        # it closed prevents the organisation-only flag from enabling personal
        # email sign-in through a direct POST to the provider selection page.
        if match and match.url_name == "account_login" and request.method == "POST" and not settings.EMAIL_LOGIN_ENABLED:
            return HttpResponseNotFound("This sign-in method is unavailable.")

        organisation = organisation_account(request.user)
        if organisation is not None and match:
            process = request.POST.get("process", request.GET.get("process"))
            changing_email = match.url_name in {
                "account_email", "account_confirm_email", "account_email_verification_sent",
            } and request.method == "POST"
            connecting_provider = process == "connect" and match.url_name in {
                "orcid_login", "github_login", "gitlab_login", "openid_connect_login",
            }
            if changing_email or connecting_provider:
                return render(request, "403.html", {
                    "permission_message": "Organisation accounts sign in with their approved email address. Contact the moderators to change that address.",
                }, status=403)
        if match and match.url_name in {
            "orcid_login", "github_login", "gitlab_login", "openid_connect_login",
        } and any(key in request.GET or key in request.POST for key in ("scope", "auth_params")):
            # allauth otherwise permits these request parameters to expand
            # consent or replace client_id/redirect_uri. Provider configuration
            # is server-controlled; verification uses provider.redirect() itself.
            return HttpResponseBadRequest("This sign-in request is invalid.")
        if request.method != "POST" or not match or match.namespace != "admin" or match.url_name != "login":
            return None
        # This middleware follows CsrfViewMiddleware, so invalid cross-site
        # requests cannot consume a target account's login allowance.
        limited = limit_action(request, action="admin_login_network", key=admin_network_key(request), retry_after=300)
        if limited:
            return limited
        account = unicodedata.normalize("NFKC", request.POST.get("username", "")).strip().casefold()[:254]
        return limit_action(request, action="admin_login_account", key=f"admin-account:{account}", retry_after=300)

    def __call__(self, request):
        path = request.path
        from django.contrib.auth import logout
        from .organisation_login import organisation_access_denied
        from .approval import denied_approval_status

        # Revoke existing sessions on a moderation change as well as preventing
        # new logins. Do not rely solely on the editable auth-user active flag.
        if organisation_access_denied(request.user) or denied_approval_status(request.user):
            logout(request)
        provider = next((name for name in ("orcid", "github", "gitlab") if path.startswith(f"/accounts/{name}/")), None)
        if provider and provider not in {item.id for item in get_adapter(request).list_providers(request)}:
            return HttpResponseNotFound("This sign-in method is unavailable.")
        email_route = path.startswith(("/accounts/signup/", "/accounts/email/", "/accounts/confirm-email/", "/accounts/login/code/"))
        organisation_confirmation = False
        if path == "/accounts/login/code/confirm/" and not settings.EMAIL_LOGIN_ENABLED:
            from .organisation_login import organisation_code_confirmation_allowed
            organisation_confirmation = organisation_code_confirmation_allowed(request)
        if path.startswith("/accounts/password/") or (email_route and not settings.EMAIL_LOGIN_ENABLED and not organisation_confirmation):
            return HttpResponseNotFound("This sign-in method is unavailable.")
        response = self.get_response(request)
        # Every HTML page can contain account navigation and a logout CSRF
        # token, including public content and error pages. Never share/cache it.
        html_response = response.get("Content-Type", "").startswith("text/html")
        if html_response or path.startswith(("/accounts/", "/submissions/", "/verify/", "/admin/")):
            add_never_cache_headers(response)
            # Keep same-origin form Origin/Referer checks working. External
            # navigation receives no referrer.
            response["Referrer-Policy"] = "same-origin"
        # Browsers may apply form-action to the OAuth POST's redirect target.
        # Permit only known login origins, never origins supplied in a request.
        form_origins = " ".join(settings.AUTHENTICATION_FORM_ORIGINS)
        script_sources = "'self'"
        connect_sources = "'self'"
        if getattr(request, "plausible_tracking", False) and response.status_code == 200:
            script_sources += f" https://plausible.io/js/{settings.PLAUSIBLE_SCRIPT_ID}.js"
            connect_sources += " https://plausible.io/api/event"
        response["Content-Security-Policy"] = f"default-src 'self'; script-src {script_sources}; style-src 'self'; img-src 'self' data:; connect-src {connect_sources}; form-action 'self' {form_origins}; frame-ancestors 'none'; base-uri 'self'"
        return response
