from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.html import format_html
import json

from .models import Submission, SubmissionRevision


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "revision", "updated_at")
    search_fields = ("title",)
    readonly_fields = [field.name for field in Submission._meta.fields] + ["editors"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SubmissionRevision)
class SubmissionRevisionAdmin(admin.ModelAdmin):
    list_display = ("submission", "number", "status", "created_at", "reviewed_by")
    list_filter = ("status",)
    search_fields = ("submission__title",)
    fields = ("submission", "number", "submitted_by", "snapshot_display", "status", "review_note", "created_at", "reviewed_by", "reviewed_at")
    readonly_fields = ("submission", "number", "submitted_by", "snapshot_display", "status", "created_at", "reviewed_by", "reviewed_at")
    actions = ("approve_selected", "request_changes")

    @admin.display(description="Exact submitted details")
    def snapshot_display(self, obj):
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", json.dumps(obj.snapshot, ensure_ascii=False, indent=2))

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
            except ValidationError as error:
                self.message_user(request, "; ".join(error.messages), level=messages.ERROR)
