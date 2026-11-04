"""Deliver a bounded batch from the durable notification queue."""
from django.core.management.base import BaseCommand, CommandError

from submissions.notifications import process_notification_queue
from accounts.organisation_notifications import process_organisation_notifications


class Command(BaseCommand):
    help = "Deliver bounded batches of contributor and organisation decision notices; print aggregate counts only."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=20)

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if not 1 <= batch_size <= 20:
            raise CommandError("Batch size must be between 1 and 20.")
        try:
            counts = process_notification_queue(batch_size=batch_size)
            organisations = process_organisation_notifications(batch_size=batch_size)
            for key in ("sent", "failed", "remaining"):
                counts[key] += organisations[key]
            summary = ", ".join(
                f"{name}={int(counts[name])}"
                for name in ("sent", "failed", "suppressed", "cancelled", "expired", "remaining")
            )
        except Exception:
            # Backend exception strings can include addresses or SMTP credentials.
            raise CommandError("Notification delivery could not complete; retry later.") from None
        self.stdout.write(summary)
        if counts["failed"]:
            # The supervisor discards output, so a nonzero status must also
            # signal SMTP failures and timed-out claims. Terminal jobs remain
            # failed; restarting the worker never repeats an ambiguous send.
            raise CommandError("One or more notifications failed; attempted messages will not be retried.")
