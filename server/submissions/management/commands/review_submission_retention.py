import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from submissions.retention_review import retention_candidates


class Command(BaseCommand):
    help = "List never-accepted submissions inactive for six calendar months. Read-only; see RETENTION.md."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Return minimal JSON for the restricted review record.")

    def handle(self, *args, **options):
        try:
            archive = json.loads(Path(settings.ARCHIVE_PATH).read_text(encoding="utf-8"))
            public_ids = {project["id"] for project in archive["projects"]}
        except (AttributeError, OSError, ValueError, KeyError, TypeError) as error:
            raise CommandError("Cannot check the static archive; resolve that before preparing a retention review.") from error
        now = timezone.now()
        rows = list(retention_candidates(now=now, static_public_ids=public_ids))
        if options["json"]:
            self.stdout.write(json.dumps({"reviewed_at": now.isoformat(), "read_only": True, "candidates": rows}, indent=2))
            return
        self.stdout.write("Read-only retention review. No notices sent and no data changed.")
        for row in rows:
            self.stdout.write(f"{row['submission_id']} last_activity={row['last_activity']} pending={row['pending_revisions']} revisions={row['revision_count']}")
        self.stdout.write(f"{len(rows)} candidate(s). Review necessity, record the decision, and give the owner 30 days' notice before any proposed removal. See RETENTION.md.")
