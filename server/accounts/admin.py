import re

from allauth.mfa.models import Authenticator
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html, format_html_join


class AccountUserAdmin(UserAdmin):
    list_display = (
        "account_name", "verified_emails", "connected_identities", "is_staff", "is_active", "last_login",
    )
    search_fields = UserAdmin.search_fields + (
        "emailaddress__email", "socialaccount__provider", "socialaccount__uid",
    )
    search_help_text = "Search by account name, email address, sign-in provider or connected account ID."
    readonly_fields = UserAdmin.readonly_fields + ("verified_emails", "connected_identities")
    fieldsets = UserAdmin.fieldsets + (
        ("Connected account details", {
            "fields": ("verified_emails", "connected_identities"),
            "description": (
                "Private account information. Only email addresses verified by the account holder "
                "are shown here. Provider-only accounts may have no email address. "
                "Contributor contact addresses are separate from account identities."
            ),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("emailaddress_set", "socialaccount_set")

    @admin.display(description="Account", ordering="username")
    def account_name(self, user):
        return user.get_full_name() or user.username

    @admin.display(description="Verified private email")
    def verified_emails(self, user):
        addresses = sorted(
            (address for address in user.emailaddress_set.all() if address.verified),
            key=lambda address: (not address.primary, address.email),
        )
        if not addresses:
            return "No verified email"
        return format_html_join("", "<div>{}</div>", ((address.email,) for address in addresses))

    @admin.display(description="Connected identities")
    def connected_identities(self, user):
        labels = {"orcid": "ORCID", "github": "GitHub", "gitlab": "GitLab", "institution": "Institution"}
        accounts = sorted(user.socialaccount_set.all(), key=lambda account: (account.provider, account.uid))
        if not accounts:
            return "No connected identities"
        identities = []
        for account in accounts:
            identifier = account.uid
            if account.provider == "orcid" and re.fullmatch(r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]", identifier):
                identifier = format_html(
                    '<a href="https://orcid.org/{}" target="_blank" rel="noopener noreferrer">{}</a>',
                    identifier, identifier,
                )
            identities.append((labels.get(account.provider, account.provider), identifier))
        return format_html_join("", "<div>{}: {}</div>", identities)


User = get_user_model()
admin.site.unregister(User)
admin.site.register(User, AccountUserAdmin)

# Authenticator material must not be viewed, replaced, or reset in ordinary admin.
if admin.site.is_registered(Authenticator):
    admin.site.unregister(Authenticator)
