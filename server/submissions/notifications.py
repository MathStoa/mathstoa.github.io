"""One notice per submitted revision to the moderation mailbox.

The notice names the project and links to the review page; it carries nothing
about the submitter's account. A missing mailbox or SMTP setting disables it,
and a failed send is logged and never blocks the submission.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def moderation_address():
    address = getattr(settings, "MODERATION_NOTIFY_EMAIL", "") or ""
    if not address or not settings.DEFAULT_FROM_EMAIL:
        return ""
    if settings.EMAIL_BACKEND.endswith("smtp.EmailBackend") and not settings.EMAIL_HOST:
        return ""  # The production backend cannot send without a host.
    return address


def notify_moderators(revision):
    address = moderation_address()
    if not address:
        return False
    title = " ".join(str(revision.submission.title).split()) or "Untitled submission"
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    link = base + reverse("admin:submissions_submissionrevision_change", args=[revision.pk])
    queue = base + reverse("admin:submissions_submissionrevision_changelist") + "?status__exact=pending"
    when = timezone.localtime(revision.created_at or timezone.now()).strftime("%d %B %Y, %H:%M %Z")
    subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Submission awaiting review: {title}"
    body = (
        f'Revision {revision.number} of "{title}" was submitted for editorial review on {when}.\n\n'
        f"Review it: {link}\n"
        f"Pending queue: {queue}\n\n"
        "Decide in the administration interface and record a review note: the submitter "
        "reads the outcome on their draft page. This notice names no account.\n"
    )
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [address], fail_silently=False)
    except Exception:
        logger.warning("Moderation notice for revision %s could not be sent.", revision.pk)
        return False
    return True
