import uuid

from django.conf import settings
from django.db import models


METHODS = [("file", "Verification file"), ("github", "GitHub authorization")]


class VerificationAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey("submissions.Submission", on_delete=models.CASCADE, related_name="verification_attempts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    method = models.CharField(max_length=10, choices=METHODS)
    repository_url = models.URLField(max_length=500)
    repository_id = models.PositiveBigIntegerField()
    repository_owner_id = models.PositiveBigIntegerField()
    full_name = models.CharField(max_length=250)
    orcid = models.CharField(max_length=50, blank=True)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["submission", "user", "expires_at"])]


class RepositoryVerification(models.Model):
    attempt = models.OneToOneField(VerificationAttempt, on_delete=models.PROTECT, related_name="proof")
    submission = models.ForeignKey("submissions.Submission", on_delete=models.CASCADE, related_name="repository_verifications")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    method = models.CharField(max_length=10, choices=METHODS)
    repository_url = models.URLField(max_length=500)
    repository_id = models.PositiveBigIntegerField()
    repository_owner_id = models.PositiveBigIntegerField()
    full_name = models.CharField(max_length=250)
    orcid = models.CharField(max_length=50, blank=True)
    github_user_id = models.PositiveBigIntegerField(null=True, blank=True)
    github_login = models.CharField(max_length=100, blank=True)
    role = models.CharField(max_length=20, blank=True)
    commit_sha = models.CharField(max_length=64, blank=True)
    verified_at = models.DateTimeField(auto_now_add=True)
    valid_until = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-verified_at", "-pk"]
        indexes = [models.Index(fields=["submission", "user", "valid_until"])]
