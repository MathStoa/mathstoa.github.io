from django.conf import settings
from allauth.socialaccount.adapter import get_adapter
from django.http import HttpResponseNotFound
from django.utils.cache import add_never_cache_headers


class AccountPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        provider = next((name for name in ("orcid", "github", "gitlab") if path.startswith(f"/accounts/{name}/")), None)
        if provider and provider not in {item.id for item in get_adapter(request).list_providers(request)}:
            return HttpResponseNotFound("This sign-in method is unavailable.")
        email_route = path.startswith(("/accounts/signup/", "/accounts/email/", "/accounts/confirm-email/", "/accounts/login/code/"))
        if path.startswith("/accounts/password/") or (email_route and not settings.EMAIL_LOGIN_ENABLED):
            return HttpResponseNotFound("This sign-in method is unavailable.")
        response = self.get_response(request)
        if path.startswith(("/accounts/", "/submissions/", "/verify/", "/admin/")):
            add_never_cache_headers(response)
            # Keep same-origin form Origin/Referer checks working. External
            # navigation receives no referrer, including invitation paths.
            response["Referrer-Policy"] = "same-origin"
        # Browsers may apply form-action to the OAuth POST's redirect target.
        # Permit only known login origins, never origins supplied in a request.
        form_origins = " ".join(settings.AUTHENTICATION_FORM_ORIGINS)
        response["Content-Security-Policy"] = f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'self' {form_origins}; frame-ancestors 'none'; base-uri 'self'"
        return response
