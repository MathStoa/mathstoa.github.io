"""Notices sent when a revision is submitted for review.

Moderators get one notice naming the project and linking to the review page.
Each listed contributor gets a separate confirmation at their private email,
which also tells them how to object to being listed. Neither carries anything
about the submitter's account. A missing mailbox or SMTP setting disables
them, and a failed send is logged and never blocks the submission.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMessage, send_mail
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def can_send():
    if not settings.DEFAULT_FROM_EMAIL:
        return False
    # The production backend cannot send without a host.
    return not (settings.EMAIL_BACKEND.endswith("smtp.EmailBackend") and not settings.EMAIL_HOST)


def moderation_address():
    address = getattr(settings, "MODERATION_NOTIFY_EMAIL", "") or ""
    return address if address and can_send() else ""


def _title(revision):
    return " ".join(str(revision.snapshot.get("title") or revision.submission.title).split()) or "Untitled submission"


def notify_moderators(revision):
    address = moderation_address()
    if not address:
        return False
    title = _title(revision)
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


def notify_contributors(revision):
    """Send one message per listed contributor; no recipient sees another's address."""
    if not can_send():
        return 0
    people = [person for person in revision.snapshot.get("collaborators", []) if person.get("email")]
    if not people:
        return 0
    title = _title(revision)
    names = "\n".join(f"- {person['name']}" for person in revision.snapshot["collaborators"])
    when = timezone.localtime(revision.created_at or timezone.now()).strftime("%d %B %Y")
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    contact = settings.PUBLIC_CONTACT_EMAIL
    subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}You are listed as a contributor to \"{title}\""
    sent = 0
    for person in people:
        body = (
            f"Dear {person['name']},\n\n"
            f'On {when}, the project "{title}" was submitted for review to mathΣτοά ({base}), '
            "an archive of open mathematical research projects. You are listed as a contributor, together with:\n\n"
            f"{names}\n\n"
            "If the project is accepted, its listing will show each contributor's name and ORCID iD. "
            "Your email address stays private and is used only to contact you about this project.\n\n"
            "If you should not be listed, or your details are wrong, reply to "
            f"{contact or 'our contact address'} and we will correct or remove them. "
            f"How we handle your information: {base}{reverse('privacy')}\n"
        )
        try:
            EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [person["email"]], reply_to=[contact] if contact else None).send()
            sent += 1
        except Exception:
            logger.warning("Contributor confirmation for revision %s could not be sent.", revision.pk)
    return sent
