"""Private submission and moderation notices.

Moderators get one notice naming the project and linking to its administration page.
Each listed contributor gets a separate confirmation at their private email,
which also tells them how to object to being listed. Neither carries anything
about the submitter's account. A missing mailbox or SMTP setting disables
them, and a failed send is logged and never blocks the submission.
Change requests go to the same frozen contributor contacts as submission
confirmations. Feedback stays on the owner's private draft page.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMessage, send_mail
from django.urls import reverse
from django.utils import timezone

from .models import SubmissionRevision, display_name

logger = logging.getLogger(__name__)


def can_send():
    if not settings.DEFAULT_FROM_EMAIL:
        return False
    if settings.EMAIL_BACKEND.endswith("smtp.EmailBackend"):
        if not settings.EMAIL_HOST:
            return False
        # Notification emails can be enabled independently of email sign-in.
        # Enforce this here too, for all three notification paths, even when
        # settings are overridden after the production startup validation.
        if not settings.DEBUG and bool(settings.EMAIL_USE_TLS) == bool(settings.EMAIL_USE_SSL):
            return False
    return True


def moderation_address():
    address = getattr(settings, "MODERATION_NOTIFY_EMAIL", "") or ""
    return address if address and can_send() else ""


def _title(revision):
    return " ".join(str(revision.snapshot.get("title") or revision.submission.title).split()) or "Untitled submission"


def contributor_contacts(revision):
    """Use the submitted contacts for both confirmation and change notices."""
    return [person for person in revision.snapshot.get("collaborators", []) if person.get("email")]


def notify_changes_requested(revision_id):
    """Called once, after the pending-to-rejected decision commits; no retries."""
    revision = SubmissionRevision.objects.select_related("submission").filter(pk=revision_id, status="rejected").first()
    if revision is None:
        return 0
    people = contributor_contacts(revision)
    if not people:
        logger.warning("Change-request notices for revision %s skipped: no contributor emails.", revision.pk)
        return 0
    if not can_send():
        logger.warning("Change-request notices for revision %s skipped: email delivery is not configured.", revision.pk)
        return 0
    title = _title(revision)
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Changes requested: {title}"
    body = (
        f'A moderator has requested changes to revision {revision.number} of "{title}" on math-stoa.\n\n'
        "You are receiving this notice because you are listed as a contributor in this submitted version.\n\n"
        "Only the project's owner can read the moderation feedback on the private submission page. "
        "The owner should sign in to their submissions, edit and save a new draft, then submit it again for moderation:\n"
        f"{base}{reverse('submissions:list')}\n\n"
        "Being listed as a contributor does not grant access to the submission or account. "
        "If you are not the owner, please coordinate any changes with the person who submitted the project.\n\n"
        f"How we handle your information: {base}{reverse('privacy')}\n"
    )
    contact = settings.PUBLIC_CONTACT_EMAIL
    sent = 0
    for person in people:
        try:
            delivered = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, [person["email"]], reply_to=[contact] if contact else None).send()
            if not delivered:
                raise RuntimeError("Email backend did not accept the message.")
            sent += 1
        except Exception:
            # Do not expose addresses or SMTP errors, and continue to the next
            # contributor even if this delivery failed.
            logger.warning("A change-request notice for revision %s could not be sent.", revision.pk)
    return sent


def notify_moderators(revision):
    address = moderation_address()
    if not address:
        return False
    title = _title(revision)
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    link = base + reverse("admin:submissions_submissionrevision_change", args=[revision.pk])
    queue = base + reverse("admin:submissions_submissionrevision_changelist") + "?status__exact=pending"
    when = timezone.localtime(revision.created_at or timezone.now()).strftime("%d %B %Y, %H:%M %Z")
    subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Submission awaiting moderation: {title}"
    body = (
        f'Revision {revision.number} of "{title}" was submitted for moderation on {when}.\n\n'
        f"Open it: {link}\n"
        f"Pending queue: {queue}\n\n"
        "Decide in the administration interface and record a note: the submitter "
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
    people = contributor_contacts(revision)
    if not people:
        return 0
    title = _title(revision)
    names = "\n".join(f"- {display_name(person['name'])}" for person in revision.snapshot["collaborators"])
    when = timezone.localtime(revision.created_at or timezone.now()).strftime("%d %B %Y")
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    contact = settings.PUBLIC_CONTACT_EMAIL
    subject = f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}You are listed as a contributor to \"{title}\""
    sent = 0
    for person in people:
        body = (
            f"Dear {person['name']},\n\n"
            f'On {when}, the project "{title}" was submitted to math-stoa ({base}), '
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
