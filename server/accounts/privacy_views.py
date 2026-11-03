"""Privacy downloads use the signed-in account; confirmations are never saved."""
from functools import wraps
from urllib.parse import urlencode

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .middleware import limit_action
from .privacy_services import (
    REQUEST_ACTIONS, REQUEST_BUNDLE_MAX_BYTES, REQUEST_TEXT_MAX_LENGTH,
    PrivacyRequestError, account_export, create_request_bundle, verify_request_bundle,
)
from .staff_mfa import fresh_bound_first_factor, has_primary_factor, has_recent_mfa, security_required


class PrivacyRequestForm(forms.Form):
    action = forms.ChoiceField(choices=REQUEST_ACTIONS, label="What would you like us to review?")
    details = forms.CharField(max_length=REQUEST_TEXT_MAX_LENGTH, widget=forms.Textarea(attrs={"rows": 6}), label="Your request", help_text="Describe the information or change. Do not include passwords, sign-in codes or recovery codes.")
    reply_to = forms.EmailField(required=False, max_length=254, label="Reply address (optional)", help_text="Use an address where you want the operator to reply. It stays in this request file and is not added to your account.")
    confirm = forms.BooleanField(label="I confirm this is the request I want the operator to review, and authorize replies to the address above if supplied.")


class VerifyPrivacyRequestForm(forms.Form):
    bundle = forms.CharField(max_length=REQUEST_BUNDLE_MAX_BYTES, widget=forms.Textarea(attrs={"rows": 10}), label="Contents of the request file", help_text="Paste the JSON attachment. Verification does not execute the request or save its contents.")


def fresh_privacy_access(view):
    @wraps(view)
    def guarded(request, *args, **kwargs):
        if not fresh_bound_first_factor(request):
            return security_required(request, message=(
                "To download account data or confirm a privacy request, sign out and sign in "
                "again using an existing method, then continue within five minutes."
            ))
        if (request.user.is_staff or has_primary_factor(request.user)) and not has_recent_mfa(request):
            name = "mfa_reauthenticate" if has_primary_factor(request.user) else "mfa_activate_totp"
            url = reverse(name) + "?" + urlencode({"next": reverse("account_privacy")})
            return security_required(request, message="Confirm with your authenticator before continuing with account data or privacy requests.", confirm_url=url)
        return view(request, *args, **kwargs)
    return guarded


def _page(request, *, form=None, status=200):
    return render(request, "account/privacy_tools.html", {
        "form": form if form is not None else PrivacyRequestForm(),
        "can_verify": request.user.is_staff and request.user.has_perm("auth.view_user"),
    }, status=status)


def _download(data, filename):
    response = JsonResponse(data, json_dumps_params={"ensure_ascii": False, "sort_keys": True, "indent": 2})
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@never_cache
@login_required
@require_GET
def privacy_tools(request):
    return _page(request)


@never_cache
@sensitive_post_parameters()
@sensitive_variables()
@login_required
@require_POST
@fresh_privacy_access
def download_account_data(request):
    limited = limit_action(request, action="privacy_export", user=request.user, retry_after=3600)
    if limited:
        return limited
    return _download(account_export(request.user), "mathstoa-account-data.json")


@never_cache
@sensitive_post_parameters()
@sensitive_variables()
@login_required
@require_POST
@fresh_privacy_access
def download_privacy_request(request):
    limited = limit_action(request, action="privacy_confirmation", user=request.user, retry_after=3600)
    if limited:
        return limited
    form = PrivacyRequestForm(request.POST)
    if not form.is_valid():
        return _page(request, form=form, status=400)
    if not settings.PUBLIC_CONTACT_EMAIL.strip():
        form.add_error(None, "Privacy requests are unavailable until the operator configures a contact address.")
        return _page(request, form=form, status=400)
    bundle = create_request_bundle(
        request.user, action=form.cleaned_data["action"], details=form.cleaned_data["details"],
        reply_to=form.cleaned_data["reply_to"],
    )
    return _download(bundle, "mathstoa-privacy-request.json")


@never_cache
@sensitive_post_parameters()
@sensitive_variables()
@staff_member_required
@require_http_methods(["GET", "POST"])
def verify_privacy_request(request):
    # The /admin/ middleware requires actual MFA on every request. Ordinary
    # review staff also need explicit permission to inspect account identities.
    if not request.user.has_perm("auth.view_user"):
        raise PermissionDenied
    form = VerifyPrivacyRequestForm(request.POST if request.method == "POST" else None)
    verified = None
    if request.method == "POST":
        limited = limit_action(request, action="privacy_verify", user=request.user, retry_after=300)
        if limited:
            return limited
        if form.is_valid():
            try:
                verified = verify_request_bundle(form.cleaned_data["bundle"])
            except PrivacyRequestError as exc:
                form.add_error("bundle", str(exc))
            else:
                # Do not echo the proof or save it in the operator's session.
                form = VerifyPrivacyRequestForm()
    return render(request, "account/verify_privacy_request.html", {"form": form, "verified": verified})
