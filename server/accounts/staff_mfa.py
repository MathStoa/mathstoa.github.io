"""Require evidence from this user's session, not merely an enrolled device."""
import math
import time
from urllib.parse import urlencode

from allauth.account.authentication import get_authentication_records
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin


def _recent(record, age):
    at = record.get("at")
    return (
        isinstance(at, (int, float)) and not isinstance(at, bool)
        and math.isfinite(at) and 0 <= time.time() - at <= age
    )


def _records(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return []
    return [r for r in get_authentication_records(request, request.user) if isinstance(r, dict)]


def fresh_bound_first_factor(request):
    """A real sign-in to this account, never the provider currently being linked."""
    return any(
        record.get("method") in {"socialaccount", "code"}
        and _recent(record, settings.ACCOUNT_SECURITY_FRESH_LOGIN_MAX_AGE)
        for record in _records(request)
    )


def has_primary_factor(user):
    return user.is_authenticated and Authenticator.objects.filter(
        user=user, type=Authenticator.Type.TOTP,
    ).exists()


def has_recent_mfa(request):
    if not has_primary_factor(request.user):
        return False
    for record in _records(request):
        pk = record.get("id")
        if (
            record.get("method") == "mfa"
            and record.get("type") in {Authenticator.Type.TOTP, Authenticator.Type.RECOVERY_CODES}
            and isinstance(pk, int) and not isinstance(pk, bool)
            and _recent(record, settings.STAFF_MFA_MAX_AGE)
            and Authenticator.objects.filter(
                pk=pk, user=request.user, type=record["type"],
            ).exists()
        ):
            return True
    return False


def security_required(request, *, message, confirm_url=None):
    return render(request, "account/security_required.html", {
        "security_message": message, "confirm_url": confirm_url,
    }, status=403)


def require_fresh_account_change(request):
    """Recheck at OAuth callback as well as initiation, before linking occurs."""
    if not fresh_bound_first_factor(request):
        return security_required(request, message=(
            "To change your sign-in methods, sign out and sign in again using an existing "
            "method, then make the change within five minutes. No change has been made."
        ))
    if (request.user.is_staff or has_primary_factor(request.user)) and not has_recent_mfa(request):
        target = "mfa_reauthenticate" if has_primary_factor(request.user) else "mfa_activate_totp"
        return security_required(request, message=(
            "Confirm your account with your authenticator before changing sign-in "
            "methods. No change has been made."
        ), confirm_url=reverse(target))
    return None


class StaffSecurityMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        name = match.url_name or ""
        # Only changes need a fresh first factor. Reading the account page does not.
        process = request.POST.get("process", request.GET.get("process")) if request.method == "POST" else request.GET.get("process")
        changing_email = request.user.is_authenticated and name in {
            "account_email", "account_confirm_email", "account_email_verification_sent",
        }
        if (
            request.path.startswith("/accounts/")
            and (process == "connect" or (request.method == "POST" and (
                name == "socialaccount_connections" or changing_email
            )))
        ):
            return require_fresh_account_change(request)

        admin_view = "admin" in match.app_names or request.path.startswith("/admin/")
        if admin_view:
            if not request.user.is_authenticated:
                return redirect_to_login(reverse("admin:index"), settings.LOGIN_URL)
            if not request.user.is_active or not request.user.is_staff:
                return HttpResponseForbidden("Staff access is required.")
            # Signing out must remain possible even if a factor was revoked.
            if name == "logout":
                return None
            if not has_primary_factor(request.user):
                return self._enrollment(request)
            if not has_recent_mfa(request):
                return self._challenge(request)

        if request.user.is_authenticated and name.startswith("mfa_"):
            if not request.user.is_active:
                return HttpResponseForbidden("This account is inactive.")
            if has_primary_factor(request.user):
                # The challenge must be reachable before a verified MFA record exists.
                if name != "mfa_reauthenticate" and not has_recent_mfa(request):
                    return self._challenge(request)
            elif name not in {"mfa_index", "mfa_activate_totp"}:
                return self._enrollment(request)
            elif not fresh_bound_first_factor(request):
                return self._fresh_login_required(request)
        return None

    def _fresh_login_required(self, request):
        return security_required(request, message=(
            "Before setting up an authenticator, sign out and sign in again using an "
            "existing sign-in method. Start setup within five minutes."
        ))

    def _enrollment(self, request):
        if not fresh_bound_first_factor(request):
            return self._fresh_login_required(request)
        target = reverse("mfa_activate_totp")
        if request.method not in {"GET", "HEAD"}:
            return security_required(request, message=(
                "Set up your authenticator before continuing. "
                "Your change has not been applied."
            ), confirm_url=target)
        return HttpResponseRedirect(target)

    def _challenge(self, request):
        target = reverse("mfa_reauthenticate") + "?" + urlencode({"next": request.path})
        if request.method not in {"GET", "HEAD"}:
            # Never save/replay a pending privileged mutation after reauthentication.
            return security_required(request, message=(
                "Confirm with your authenticator, then submit the change again. "
                "Your change has not been applied."
            ), confirm_url=target)
        return HttpResponseRedirect(target)
