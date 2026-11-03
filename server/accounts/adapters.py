import secrets

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.core.exceptions import ValidationError


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return settings.EMAIL_LOGIN_ENABLED

    def authenticate(self, request, **credentials):
        # Public accounts use provider sign-in or email codes. Staff passwords
        # remain available only through Django's separate admin sign-in.
        return None


class MinimalSocialAccountAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        # Run before allauth's lookup, which updates existing SocialAccount rows.
        # Retain only provider + stable uid, including on repeat sign-ins.
        sociallogin.account.extra_data = {}
        sociallogin.email_addresses = []
        sociallogin.phone = None
        return super().populate_user(request, sociallogin, {"username": "author-" + secrets.token_hex(12)})

    def is_open_for_signup(self, request, sociallogin):
        return True

    def validate_disconnect(self, account, accounts):
        enabled_providers = {provider.sub_id for provider in self.list_providers(self.request)}
        another_provider = any(other.pk != account.pk and other.provider in enabled_providers for other in accounts)
        email_access = settings.EMAIL_LOGIN_ENABLED and EmailAddress.objects.filter(user=account.user, verified=True).exists()
        if not another_provider and not email_access:
            raise ValidationError("Connect another sign-in method before removing this one.")
