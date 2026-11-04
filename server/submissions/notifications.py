"""Bounded contributor-only email outbox. Web requests never contact SMTP.

Quotas suppress excess notices, rather than building a delayed flood. A delivery
is attempted once: an interrupted or ambiguous SMTP send is never retried.
Addresses are resolved from the frozen submitted revision, not copied into jobs.
"""
import logging
from datetime import timedelta
from time import monotonic

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import NotificationDelivery, NotificationQueueLock, SubmissionRevision, display_name

logger = logging.getLogger(__name__)
RECIPIENT_HOURLY_LIMIT = 1  # Per kind; creation, updates and changes requested are separate.
RECIPIENT_DAILY_LIMIT = 5
ACCOUNT_DAILY_LIMIT = 100
GLOBAL_HOURLY_LIMIT = 100
GLOBAL_DAILY_LIMIT = 500
MAX_PENDING = 500
MAX_RETAINED = 5000
QUEUE_MAX_AGE = timedelta(hours=24)
RECORD_RETENTION = timedelta(days=7)
SENDING_MAX_AGE = timedelta(minutes=10)


def can_send():
    if not settings.DEFAULT_FROM_EMAIL:
        return False
    if settings.EMAIL_BACKEND.endswith("smtp.EmailBackend"):
        if not settings.EMAIL_HOST:
            return False
        if not settings.DEBUG and bool(settings.EMAIL_USE_TLS) == bool(settings.EMAIL_USE_SSL):
            return False
    return True


def _address(value):
    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    if not value or len(value) > 254:
        return None
    try:
        validate_email(value)
    except ValidationError:
        return None
    return value


def _digest(address):
    return salted_hmac("mathstoa.notification-recipient.v1", address, algorithm="sha256").hexdigest()


def contributor_contacts(revision):
    """Deduplicate and validate old as well as current snapshot contacts."""
    return _contacts(revision.snapshot.get("collaborators", []))


def _contacts(people):
    seen = set()
    if not isinstance(people, list):
        return []
    contacts = []
    for person in people[:100]:
        if not isinstance(person, dict):
            continue
        address = _address(person.get("email"))
        if address and address not in seen:
            seen.add(address)
            contacts.append({"name": str(person.get("name") or "Contributor")[:200], "email": address})
    return contacts


def _lock_queue():
    # UPDATE acquires a row lock on PostgreSQL and a write lock on SQLite.
    # Enqueuers acquire their revision lock first; workers never lock a revision.
    NotificationQueueLock.objects.get_or_create(pk=1)
    NotificationQueueLock.objects.filter(pk=1).update(version=F("version") + 1)


def _cleanup(now):
    jobs = NotificationDelivery.objects
    expired = jobs.filter(status="queued", created_at__lt=now - QUEUE_MAX_AGE).update(status="expired", finished_at=now)
    # A crashed worker may have sent its message. Never retry such a claim.
    failed = jobs.filter(status="sending", attempted_at__lt=now - SENDING_MAX_AGE).update(status="failed", finished_at=now)
    jobs.filter(created_at__lt=now - RECORD_RETENTION).exclude(status__in=["queued", "sending"]).delete()
    return expired, failed


def _quota_available(*, digest, account_id, kind, now, attempted=False):
    # Enqueue reservations include every recently created job, including failures.
    # Delivery separately checks actual attempts so a slow backlog cannot burst.
    field = "attempted_at" if attempted else "created_at"
    recent = NotificationDelivery.objects.filter(**{field + "__gte": now - timedelta(days=1)})
    hourly = recent.filter(**{field + "__gte": now - timedelta(hours=1)})
    return (
        hourly.filter(recipient_digest=digest, kind=kind).count() < RECIPIENT_HOURLY_LIMIT
        and recent.filter(recipient_digest=digest).count() < RECIPIENT_DAILY_LIMIT
        and recent.filter(account_id=account_id).count() < ACCOUNT_DAILY_LIMIT
        and hourly.count() < GLOBAL_HOURLY_LIMIT
        and recent.count() < GLOBAL_DAILY_LIMIT
    )


def _enqueue(revision_id, kind):
    with transaction.atomic():
        revision = SubmissionRevision.objects.select_for_update(of=("self",)).select_related("submission").filter(pk=revision_id).first()
        if revision is None or (kind == "changes" and revision.status != "rejected"):
            return 0
        contacts = contributor_contacts(revision)
        if not contacts:
            logger.warning("Contributor notices for revision %s skipped: no contributor emails.", revision.pk)
            return 0
        if not can_send():
            logger.warning("Contributor notices for revision %s skipped: email delivery is not configured.", revision.pk)
            return 0
        _lock_queue()
        now = timezone.now()
        _cleanup(now)
        pending = NotificationDelivery.objects.filter(status__in=["queued", "sending"]).count()
        retained = NotificationDelivery.objects.count()
        keys = set(revision.notification_keys)
        added = 0
        for person in contacts:
            digest = _digest(person["email"])
            key = kind + ":" + digest
            if key in keys:
                continue
            # Bounded lifetime tombstones retain idempotency after job cleanup.
            keys.add(key)
            if pending >= MAX_PENDING or retained >= MAX_RETAINED or not _quota_available(
                digest=digest, account_id=revision.submission.owner_id, kind=kind, now=now,
            ):
                continue
            NotificationDelivery.objects.create(
                revision=revision, account_id=revision.submission.owner_id,
                kind=kind, recipient_digest=digest, created_at=now,
            )
            pending += 1
            retained += 1
            added += 1
        SubmissionRevision.objects.filter(pk=revision.pk).update(notification_keys=sorted(keys))
        return added


def notify_submission_received(revision):
    """One receipt per submitted version, including versions awaiting verification."""
    return _enqueue(revision.pk, "created" if revision.number == 1 else "updated")


def notify_contributors(revision):
    return _enqueue(revision.pk, "confirmation")


def notify_changes_requested(revision_id):
    return _enqueue(revision_id, "changes")


def _message(job, person):
    revision = job.revision
    title = " ".join(str(revision.snapshot.get("title") or "Untitled submission").split())[:120]
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    if job.kind in {"created", "updated"}:
        # Personalize an existing recipient only; account addresses never add
        # recipients or grant access to a submission. Recheck at delivery time.
        is_owner = EmailAddress.objects.filter(
            user_id=revision.submission.owner_id, email__iexact=person["email"], verified=True,
        ).exists()
        if job.kind == "created":
            subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX.rstrip()}: Submitted — {title}"
            introduction = f'{"Your submission" if is_owner else "A submission"} for “{title}” has been created on mathStoa.'
            moderation = ""
        else:
            subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX.rstrip()}: Revision — {title}"
            introduction = f'{"Your changes" if is_owner else "Changes"} to the submission for “{title}” have been saved as revision {revision.number}.'
            moderation = (
                "Changes must be moderated and approved before appearing publicly. "
                "Any previously published version remains unchanged in the meantime.\n\n"
            )
        if is_owner:
            next_steps = (
                "You can modify your submission here:\n"
                f"{base}{reverse('submissions:edit', kwargs={'pk': revision.submission_id})}\n\n"
            )
        else:
            introduction += " You’re receiving this confirmation because you’re listed as a contributor."
            next_steps = "If you should not be listed, or your details are incorrect, please reply to this email.\n\n"
        body = (
            f"Dear {display_name(person['name'])},\n\n"
            f"{introduction}\n\n"
            f"{moderation}"
            f"{next_steps}"
            "The math-stoa team\n"
        )
    elif job.kind == "changes":
        subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Changes requested: {title}"
        body = (
            f'A moderator has requested changes to revision {revision.number} of "{title}" on math-stoa.\n\n'
            "You are receiving this notice because you are listed as a contributor in this submitted version.\n\n"
            "Only the project's owner can read the moderation feedback on the private submission page. "
            "The owner should sign in to their submissions, edit the submission and submit the changes for moderation:\n"
            f"{base}{reverse('submissions:list')}\n\n"
            "Being listed as a contributor does not grant access to the submission or account. "
            "If you are not the owner, please coordinate any changes with the person who submitted the project.\n\n"
            f"How we handle your information: {base}{reverse('privacy')}\n"
        )
    else:
        names = "\n".join(f"- {display_name(str(p.get('name') or 'Contributor')[:200])}" for p in revision.snapshot.get("collaborators", [])[:100] if isinstance(p, dict))
        when = timezone.localtime(revision.created_at).strftime("%d %B %Y")
        subject = f'{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}You are listed as a contributor to "{title}"'
        body = (
            f"Dear {display_name(person['name'])},\n\n"
            f'On {when}, the project "{title}" was submitted to math-stoa ({base}), '
            "an archive of open mathematical research projects. You are listed as a contributor, together with:\n\n"
            f"{names}\n\n"
            "If the project is accepted, its listing will show each contributor's name and ORCID iD. "
            "Your email address stays private and is used only to contact you about this project.\n\n"
            "If you should not be listed, or your details are wrong, reply to "
            f"{settings.PUBLIC_CONTACT_EMAIL or 'our contact address'} and we will correct or remove them. "
            f"How we handle your information: {base}{reverse('privacy')}\n"
        )
    contact = settings.PUBLIC_CONTACT_EMAIL
    return EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [person["email"]], reply_to=[contact] if contact else None)


def _claim():
    with transaction.atomic():
        _lock_queue()
        now = timezone.now()
        job = NotificationDelivery.objects.filter(status="queued").select_related("revision", "revision__submission").order_by("created_at", "pk").first()
        if job is None:
            return None, None, None
        contacts = contributor_contacts(job.revision)
        person = next((p for p in contacts if _digest(p["email"]) == job.recipient_digest), None)
        if person is None or (job.kind == "changes" and job.revision.status != "rejected"):
            job.status = "cancelled"
        elif not _quota_available(digest=job.recipient_digest, account_id=job.account_id, kind=job.kind, now=now, attempted=True):
            job.status = "suppressed"
        else:
            job.status = "sending"
            job.attempted_at = now
        if job.status != "sending":
            job.finished_at = now
        job.save(update_fields=["status", "attempted_at", "finished_at"])
        return job, person, job.status


def process_notification_queue(*, batch_size=20):
    """Claim and reserve under a database lock; send outside it, once only.

    Counts contain no recipients or SMTP errors. The caller runs this in a
    separate worker process, never from a web view or transaction.on_commit.
    """
    counts = dict.fromkeys(("sent", "failed", "suppressed", "cancelled", "expired", "remaining"), 0)
    with transaction.atomic():
        _lock_queue()
        counts["expired"], counts["failed"] = _cleanup(timezone.now())
    deadline = monotonic() + 90
    if can_send():
        for _ in range(max(0, min(int(batch_size), 100))):
            if monotonic() >= deadline:
                break
            job, person, state = _claim()
            if job is None:
                break
            if state != "sending":
                counts[state] += 1
                continue
            status = "failed"
            try:
                if _message(job, person).send() == 1:
                    status = "sent"
            except Exception:
                # SMTP exceptions can contain email addresses and credentials.
                pass
            NotificationDelivery.objects.filter(pk=job.pk, status="sending").update(status=status, finished_at=timezone.now())
            counts[status] += 1
            if status == "failed":
                logger.warning("A contributor notification could not be delivered; it will not be retried.")
    counts["remaining"] = NotificationDelivery.objects.filter(status="queued").count()
    return counts
