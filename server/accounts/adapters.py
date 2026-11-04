import secrets

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from .policy import registration_is_open


class AccountAdapter(DefaultAccountAdapter):
    @transaction.atomic
    def save_user(self, request, user, form, commit=True):
        from .approval import register_for_approval

        new_account = user.pk is None
        user = super().save_user(request, user, form, commit=commit)
        if new_account and commit:
            register_for_approval(user)
        return user

    def _organisation_denied(self, request, user):
        from allauth.account.internal.stagekit import clear_login
        from .organisation_login import organisation_access_denied

        if organisation_access_denied(user):
            clear_login(request)
            return self.respond_user_inactive(request, user)
        return None

    def pre_login(self, request, user, **kwargs):
        return self._organisation_denied(request, user) or super().pre_login(request, user, **kwargs)

    def login(self, request, user):
        # Recheck after a code/MFA stage, since moderation can change while the
        # browser is waiting for a challenge to be completed.
        from allauth.core.exceptions import ImmediateHttpResponse
        from .approval import approval_denied_response

        # Email/code/MFA stages must finish first. Pending applicants prove
        # control of their identity but never receive an authenticated session.
        response = self._organisation_denied(request, user) or approval_denied_response(request, user)
        if response is not None:
            raise ImmediateHttpResponse(response)
        return super().login(request, user)

    def is_open_for_signup(self, request):
        return settings.EMAIL_LOGIN_ENABLED and registration_is_open()

    def authenticate(self, request, **credentials):
        # Authors and staff both use provider sign-in or verified email codes.
        return None

    def get_reauthentication_methods(self, user):
        # There is no usable password sign-in path, including for staff.
        return [method for method in super().get_reauthentication_methods(user) if method["id"] != "reauthenticate"]

    def is_reauthentication_required(self, *, app=""):
        from .staff_mfa import fresh_bound_first_factor, has_primary_factor, has_recent_mfa

        if app == "allauth.mfa" and self.request and self.request.user.is_authenticated:
            # Enrollment needs a recent existing first factor. Once enrolled,
            # only actual MFA proof qualifies, even if a password record exists.
            if has_recent_mfa(self.request):
                return False
            if not has_primary_factor(self.request.user) and fresh_bound_first_factor(self.request):
                return False
        return super().is_reauthentication_required(app=app)


class MinimalSocialAccountAdapter(DefaultSocialAccountAdapter):
    @transaction.atomic
    def save_user(self, request, sociallogin, form=None):
        from .approval import register_for_approval

        new_account = sociallogin.user.pk is None
        user = super().save_user(request, sociallogin, form=form)
        if new_account:
            register_for_approval(user)
        return user

    def pre_social_login(self, request, sociallogin):
        from allauth.core.exceptions import ImmediateHttpResponse
        from allauth.socialaccount.providers.base import AuthProcess
        from .staff_mfa import require_fresh_account_change

        data = sociallogin.state.get("data")
        verification_only = isinstance(data, dict) and "mathstoa_verification" in data
        from django.shortcuts import render
        from .organisation_login import organisation_account

        if not verification_only and (
            organisation_account(request.user) is not None
            or organisation_account(sociallogin.user) is not None
        ):
            raise ImmediateHttpResponse(render(request, "403.html", {
                "permission_message": "Organisation accounts use their approved email address to sign in. External accounts can only be used to verify repository access.",
            }, status=403))
        # The repository verification receiver always exits before linking,
        # including invalid attempts. It does not change any sign-in method.
        if sociallogin.state.get("process") == AuthProcess.CONNECT and not verification_only:
            response = require_fresh_account_change(request)
            if response is not None:
                raise ImmediateHttpResponse(response)
        return super().pre_social_login(request, sociallogin)

    def populate_user(self, request, sociallogin, data):
        # Run before allauth's lookup, which updates existing SocialAccount rows.
        # Retain only provider + stable uid, including on repeat sign-ins.
        sociallogin.account.extra_data = {}
        sociallogin.email_addresses = []
        sociallogin.phone = None
        return super().populate_user(request, sociallogin, {"username": "author-" + secrets.token_hex(12)})

    def is_open_for_signup(self, request, sociallogin):
        return registration_is_open()

    def validate_disconnect(self, account, accounts):
        enabled_providers = {provider.sub_id for provider in self.list_providers(self.request)}
        another_provider = any(other.pk != account.pk and other.provider in enabled_providers for other in accounts)
        email_access = settings.EMAIL_LOGIN_ENABLED and EmailAddress.objects.filter(user=account.user, verified=True).exists()
        if not another_provider and not email_access:
            raise ValidationError("Connect another sign-in method before removing this one.")
