from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from repository_verification.models import VerificationAttempt


class Command(BaseCommand):
    help = "Remove expired, unused verification attempts older than the retention period; preserve proof evidence."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("--days must be at least 1.")
        now = timezone.now()
        stale = VerificationAttempt.objects.filter(
            created_at__lt=now - timedelta(days=days), expires_at__lt=now,
            proof__isnull=True,
        )
        count = stale.count()
        if not options["dry_run"]:
            count, _ = stale.delete()
        self.record_count = count
        self.stdout.write(self.style.SUCCESS(f"{'Would remove' if options['dry_run'] else 'Removed'} {count} expired unused verification attempts."))
