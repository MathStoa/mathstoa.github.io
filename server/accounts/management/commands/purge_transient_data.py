from django.core.management.base import BaseCommand, CommandError

from accounts.maintenance import purge_transient_data


class Command(BaseCommand):
    help = "Remove expired operational data, empty unverified accounts after 7 days, unused never-submitted drafts after 90 days, and accounts without responsibilities after 365 days of inactivity; preserve submitted/public records."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Retention for terminal invitations and unused verification attempts (default: 30).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["days"] < 1:
            raise CommandError("--days must be at least 1")
        counts = purge_transient_data(dry_run=options["dry_run"], days=options["days"])
        if counts is None:
            self.stdout.write("Skipped: another instance is already running transient cleanup.")
            return
        action = "Would remove" if options["dry_run"] else "Removed"
        self.stdout.write(f"{action}: " + ", ".join(f"{name}={count}" for name, count in counts.items()) + ".")
