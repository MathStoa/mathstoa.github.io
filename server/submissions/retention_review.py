"""Read-only candidates for the operator's monthly retention review."""
import calendar

from django.db.models import Max

from .models import Submission


def six_months_before(value):
    month_index = value.year * 12 + value.month - 1 - 6
    year, month = divmod(month_index, 12)
    month += 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def last_activity(submission):
    dates = [submission.created_at, submission.updated_at]
    for records, fields in (
        (submission.revisions, ("created_at", "reviewed_at")),
        (submission.verification_attempts, ("created_at", "consumed_at", "revoked_at")),
        (submission.repository_verifications, ("verified_at", "revoked_at")),
    ):
        dates.extend(value for value in records.aggregate(**{field: Max(field) for field in fields}).values() if value is not None)
    return max(dates)


def retention_candidates(*, now, static_public_ids=()):
    """Never delete or treat an old pending decision as an automatic rejection."""
    cutoff = six_months_before(now)
    # Never-submitted drafts already have their separate 90-day policy.
    candidates = Submission.objects.filter(published_revision__isnull=True, revisions__isnull=False).exclude(
        revisions__status="approved",
    ).distinct().order_by("pk")
    for submission in candidates.iterator(chunk_size=200):
        if submission.public_id in static_public_ids:
            continue
        activity = last_activity(submission)
        if activity > cutoff:
            continue
        yield {
            "submission_id": str(submission.pk),
            "last_activity": activity.isoformat(),
            "pending_revisions": submission.revisions.filter(status="pending").count(),
            "revision_count": submission.revisions.count(),
        }
