"""Read-only audit listing of completed proofs; attempts and token digests stay private."""
from django.contrib import admin

from .models import RepositoryVerification


@admin.register(RepositoryVerification)
class RepositoryVerificationAdmin(admin.ModelAdmin):
    list_display = ("submission", "method", "github_login", "role", "verified_at", "valid_until", "revoked_at")
    list_filter = ("method",)
    search_fields = ("submission__title", "full_name", "github_login")
    ordering = ("-verified_at",)
    readonly_fields = [field.name for field in RepositoryVerification._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
