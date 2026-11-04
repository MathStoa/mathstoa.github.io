import re
from copy import copy

from allauth.mfa.models import Authenticator
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.template.response import TemplateResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.contrib.admin.utils import unquote

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
        from .organisation_deletion import can_delete_organisation

        if not can_delete_organisation(request.user):
            return False
        return obj is None or (
            obj.user_id != request.user.pk and not obj.user.is_staff and not obj.user.is_superuser
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Every deletion needs its own account details and typed confirmation.
        actions.pop("delete_selected", None)
        return actions

    def delete_queryset(self, request, queryset):
        raise PermissionDenied("Organisation accounts must be deleted individually.")

    def delete_model(self, request, obj):
        from .organisation_deletion import delete_organisation

        delete_organisation(obj, moderator=request.user)

    @method_decorator(csrf_protect)
    def delete_view(self, request, object_id, extra_context=None):
        from .organisation_deletion import organisation_deletion_blockers

        if request.method not in {"GET", "HEAD", "POST"}:
            return HttpResponseNotAllowed(["GET", "HEAD", "POST"])
        if not self.has_delete_permission(request):
            raise PermissionDenied
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            return self._get_obj_does_not_exist_redirect(request, self.opts, object_id)
        if not self.has_delete_permission(request, obj):
            raise PermissionDenied

        errors = []
        blockers = organisation_deletion_blockers(obj)
        status = 200
        if request.method == "POST":
            try:
                with transaction.atomic():
                    # Re-read the exact identity being confirmed and serialize
                    # against approval, deletion and decision-email delivery.
                    obj = self.get_queryset(request).select_for_update().get(pk=obj.pk)
                    if not self.has_delete_permission(request, obj):
                        raise PermissionDenied
                    if request.POST.get("confirm_name") != obj.name:
                        raise ValidationError("Type the organisation name exactly as shown to confirm deletion.")
                    blockers = organisation_deletion_blockers(obj)
                    if blockers:
                        raise ValidationError("This organisation account cannot be deleted while the listed records or responsibilities remain.")
                    obj_id, obj_display = obj.pk, str(obj)
                    # Keep the audit record useful without retaining the
                    # deleted organisation's name or contact email.
                    audit_object = copy(obj)
                    audit_object.name = f"Organisation account #{obj_id}"
                    self.log_deletions(request, [audit_object])
                    self.delete_model(request, obj)
                return self.response_delete(request, obj_display, obj_id)
            except self.model.DoesNotExist:
                return self._get_obj_does_not_exist_redirect(request, self.opts, object_id)
            except ValidationError as error:
                errors = error.messages
                status = 400
                obj = self.get_object(request, unquote(object_id))
                if obj is None:
                    return self._get_obj_does_not_exist_redirect(request, self.opts, object_id)
                blockers = organisation_deletion_blockers(obj)

        request.current_app = self.admin_site.name
        context = {
            **self.admin_site.each_context(request),
            **(extra_context or {}),
            "title": "Delete organisation account",
            "subtitle": None,
            "opts": self.opts,
            "object": obj,
            "original": obj,
            "media": self.media,
            "blockers": blockers,
            "errors": errors,
            "confirm_name": request.POST.get("confirm_name", ""),
        }
        return TemplateResponse(
            request, "admin/accounts/organisationaccount/delete_confirmation.html", context, status=status,
        )

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
