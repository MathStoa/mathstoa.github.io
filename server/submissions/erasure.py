"""Shared cleanup for owner deletion and operator-reviewed erasure."""
import json
from pathlib import Path

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from repository_verification.models import RepositoryVerification, VerificationAttempt

from .models import Submission, SubmissionRevision


def has_static_listing(submission):
    archive = json.loads(Path(settings.ARCHIVE_PATH).read_text(encoding="utf-8"))
    return any(project.get("id") == submission.public_id for project in archive.get("projects", []))


def redact_admin_log(targets):
    redacted = 0
    for model, pks in targets:
        if pks:
            redacted += LogEntry.objects.filter(
                content_type=ContentType.objects.get_for_model(model),
                object_id__in=[str(pk) for pk in pks],
            ).update(object_repr="[erased]", change_message="")
    return redacted


@transaction.atomic
def erase_submission(submission):
    submission = Submission.objects.select_for_update().get(pk=submission.pk)
    log_targets = [
        (Submission, [submission.pk]),
        (SubmissionRevision, list(submission.revisions.values_list("pk", flat=True))),
        (VerificationAttempt, list(submission.verification_attempts.values_list("pk", flat=True))),
        (RepositoryVerification, list(submission.repository_verifications.values_list("pk", flat=True))),
    ]
    # Both relations deliberately PROTECT; clear them before the cascade.
    Submission.objects.filter(pk=submission.pk).update(published_revision=None)
    RepositoryVerification.objects.filter(submission=submission).delete()
    submission.delete()
    return redact_admin_log(log_targets)
