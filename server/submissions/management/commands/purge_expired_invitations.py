from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from submissions.models import EditorInvitation


class Command(BaseCommand):
    help = "Remove invitation records that expired, were used or were revoked over 30 days ago. Existing editor permissions are unchanged."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["days"] < 1:
            raise CommandError("--days must be at least 1")
        cutoff = timezone.now() - timedelta(days=options["days"])
        stale = EditorInvitation.objects.filter(Q(expires_at__lt=cutoff) | Q(accepted_at__lt=cutoff) | Q(revoked_at__lt=cutoff))
        count = stale.count()
        if not options["dry_run"]:
            count, _ = stale.delete()
        self.record_count = count
        self.stdout.write(f"{'Would remove' if options['dry_run'] else 'Removed'} {count} old invitation records.")
