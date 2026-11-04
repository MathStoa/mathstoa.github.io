import re

from allauth.mfa.models import Authenticator
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError

from .models import OrganisationAccount
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


@admin.register(OrganisationAccount)
class OrganisationAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "website", "status", "created_at", "reviewed_by", "notification_status")
    list_filter = ("status", "notification_status")
    search_fields = ("name", "email")
    fields = ("name", "website", "description", "email", "representative_name", "authority", "user", "status", "review_note", "created_at", "reviewed_by", "reviewed_at", "notification_status", "notification_attempted_at", "decision_notified_at")
    readonly_fields = tuple(field for field in fields if field != "review_note")
    actions = ("approve_selected", "reject_selected")

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (("review_note",) if obj and obj.status != "pending" else ())

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Approve and activate selected organisation accounts", permissions=["change"])
    def approve_selected(self, request, queryset):
        self._review(request, queryset, approve=True)

    @admin.action(description="Reject selected organisation applications (requires a decision message)", permissions=["change"])
    def reject_selected(self, request, queryset):
        self._review(request, queryset, approve=False)

    def _review(self, request, queryset, *, approve):
        for application in queryset.order_by("pk"):
            try:
                application.review(request.user, approve=approve, note=application.review_note)
                self.message_user(request, f"{application.name}: {'approved and activated' if approve else 'rejected'}. The decision email is queued.")
            except ValidationError as error:
                self.message_user(request, "; ".join(error.messages), level=messages.ERROR)
