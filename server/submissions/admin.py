from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import format_html, format_html_join
import json

from repository_verification.models import RepositoryVerification

from .models import Submission, SubmissionRevision
from .notifications import can_send, contributor_contacts


class AbstractPreviewMedia:
    class Media:
        css = {"all": ("/assets/abstract-math.css",)}
        js = ("abstract-admin.js",)


@admin.register(Submission)
class SubmissionAdmin(AbstractPreviewMedia, admin.ModelAdmin):
    list_display = ("title", "owner", "revision", "updated_at")
    search_fields = ("title",)
    readonly_fields = [field.name for field in Submission._meta.fields] + ["abstract_preview"]

    @admin.display(description="Abstract preview")
    def abstract_preview(self, obj):
        return format_html('<div data-abstract>{}</div>', obj.abstract)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SubmissionRevision)
class SubmissionRevisionAdmin(AbstractPreviewMedia, admin.ModelAdmin):
    list_display = ("submission", "number", "status", "created_at", "reviewed_by")
    list_filter = ("status",)
    search_fields = ("submission__title",)
    fields = ("submission", "number", "submitted_by", "snapshot_display", "abstract_preview", "verification_display", "status", "review_note", "created_at", "reviewed_by", "reviewed_at")
    readonly_fields = ("submission", "number", "submitted_by", "snapshot_display", "abstract_preview", "verification_display", "status", "created_at", "reviewed_by", "reviewed_at")
    actions = ("approve_selected", "request_changes")

    @admin.display(description="Submitted abstract preview")
    def abstract_preview(self, obj):
        return format_html('<div data-abstract>{}</div>', obj.snapshot.get("abstract", ""))

    @admin.display(description="Exact submitted details")
    def snapshot_display(self, obj):
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", json.dumps(obj.snapshot, ensure_ascii=False, indent=2))

    @admin.display(description="Repository verification")
    def verification_display(self, obj):
        """Latest proof for the submission, for the moderator's eyes; approval re-runs the real check."""
        from repository_verification.services import has_current_verification

        def stamp(value):
            return timezone.localtime(value).strftime("%d %b %Y %H:%M %Z")

        proof = RepositoryVerification.objects.filter(submission_id=obj.submission_id).order_by("-verified_at", "-pk").first()
        if proof is None:
            return "No repository verification on record. Approval will be refused until the owner verifies the repository."
        if proof.method == "github":
            evidence = f"GitHub authorization by {proof.github_login} (GitHub user id {proof.github_user_id}, role: {proof.role})"
        else:
            evidence = f"Verification file read at commit {proof.commit_sha}"
        if proof.revoked_at:
            state = f"revoked {stamp(proof.revoked_at)}; approval will be refused until the owner verifies again"
        elif has_current_verification(obj.submission):
            state = f"current, valid until {stamp(proof.valid_until)}"
        else:
            state = "expired or revoked; approval will be refused until the owner verifies again"
        lines = [
            evidence,
            f"Repository: {proof.repository_url} (GitHub repository id {proof.repository_id}, owner id {proof.repository_owner_id})",
            f"Verified {stamp(proof.verified_at)}" + (f" with ORCID {proof.orcid}" if proof.orcid else ""),
            f"Status: {state}",
        ]
        if obj.snapshot.get("repository_url") != proof.repository_url:
            lines.append("Warning: this revision names a different repository URL from the proof. Approval will be refused.")
        return format_html("<ul style='margin:0;padding-left:18px'>{}</ul>", format_html_join("", "<li>{}</li>", ((line,) for line in lines)))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields + (("review_note",) if obj and obj.status != "pending" else ())

    @admin.action(description="Approve and publish the exact selected revisions", permissions=["change"])
    def approve_selected(self, request, queryset):
        self._review(request, queryset, approve=True)

    @admin.action(description="Request changes to the selected revisions", permissions=["change"])
    def request_changes(self, request, queryset):
        self._review(request, queryset, approve=False)

    def _review(self, request, queryset, *, approve):
        for revision in queryset.order_by("submission_id", "number"):
            try:
                revision.review(request.user, approve=approve, note=revision.review_note)
                self.message_user(request, f"Revision {revision.number}: {'published' if approve else 'changes requested'}.")
                if not approve:
                    if not contributor_contacts(revision):
                        self.message_user(request, "No email notification: this submitted revision has no contributor email addresses. The owner can read the decision on their submission page.", level=messages.WARNING)
                    elif not can_send():
                        self.message_user(request, "No email notification: email delivery is not configured. The owner can read the decision on their submission page.", level=messages.WARNING)
            except ValidationError as error:
                self.message_user(request, "; ".join(error.messages), level=messages.ERROR)
