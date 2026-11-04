"""One durable decision notice per moderated organisation application."""
from datetime import timedelta
from time import monotonic

from django.conf import settings
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone

from submissions.notifications import can_send
from .models import OrganisationAccount


def process_organisation_notifications(*, batch_size=20):
    now = timezone.now()
    records = OrganisationAccount.objects
    # An interrupted send is ambiguous: never send a second copy automatically.
    failed = records.filter(notification_status="sending", notification_attempted_at__lt=now - timedelta(minutes=10)).update(notification_status="failed")
    counts = {"sent": 0, "failed": failed, "remaining": 0}
    deadline = monotonic() + 90
    if can_send():
        for pk in records.filter(notification_status="queued").order_by("reviewed_at").values_list("pk", flat=True)[:max(0, min(batch_size, 20))]:
            if monotonic() >= deadline:
                break
            claimed = records.filter(pk=pk, notification_status="queued").update(notification_status="sending", notification_attempted_at=timezone.now())
            if not claimed:
                continue
            organisation = records.get(pk=pk)
            if organisation.status == "approved":
                outcome = "Your organisation account has been approved."
                next_step = "Sign in using a code sent to your registered email address:\n" + settings.PUBLIC_BASE_URL.rstrip("/") + reverse("organisation_login")
            else:
                outcome = "Your organisation account application has not been approved. The account remains inactive."
                next_step = "For questions about this decision, contact " + settings.PUBLIC_CONTACT_EMAIL if settings.PUBLIC_CONTACT_EMAIL else "Please contact the site moderator about this decision."
            body = f"{outcome}\n\nOrganisation: {organisation.name}\n\n{organisation.review_note}\n\n{next_step}\n"
            state = "failed"
            try:
                message = EmailMessage(
                    f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Organisation registration decision",
                    body, settings.DEFAULT_FROM_EMAIL, [organisation.email],
                )
                if message.send() == 1:
                    state = "sent"
            except Exception:
                # SMTP exception text may contain private addresses/credentials.
                pass
            records.filter(pk=pk, notification_status="sending").update(
                notification_status=state,
                decision_notified_at=timezone.now() if state == "sent" else None,
            )
            counts[state] += 1
    counts["remaining"] = records.filter(notification_status="queued").count()
    return counts
