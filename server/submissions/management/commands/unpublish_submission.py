"""Withdraw a public listing without erasing its private records."""
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from submissions.models import Submission


class Command(BaseCommand):
    help = "Unpublish one submission, retaining all revisions and verification records. Dry run unless --execute is supplied."

    def add_arguments(self, parser):
        parser.add_argument("submission_id", help="Submission UUID or public project-<hex> id.")
        parser.add_argument("--execute", action="store_true", help="Clear the public revision pointer.")
        parser.add_argument("--expected-revision", type=int, help="Refuse if the published revision database id changed since inspection.")

    def handle(self, *args, **options):
        with transaction.atomic():
            try:
                submission = Submission.objects.select_for_update().get(
                    pk=options["submission_id"].removeprefix("project-")
                )
            except (Submission.DoesNotExist, ValidationError, ValueError):
                raise CommandError("No submission has that id.") from None
            previous_id = submission.published_revision_id
            expected = options["expected_revision"]
            if expected is not None and previous_id != expected:
                raise CommandError("The published revision does not match --expected-revision. Inspect the listing again.")
            self.stdout.write(f"Submission {submission.pk}: published_revision_id={previous_id}; revisions={submission.revisions.count()}.")
            self.warn_static_archive(submission)
            if previous_id is None:
                self.stdout.write("No database-backed revision is currently published; nothing changed.")
                return
            if not options["execute"]:
                self.stdout.write("Dry run: nothing changed. Add --execute and --expected-revision with the id above to unpublish.")
                return
            submission.published_revision = None
            submission.save(update_fields=["published_revision"])
        self.stdout.write(f"Unpublished submission {submission.pk} at {timezone.now().isoformat()}; previous_published_revision_id={previous_id}. Private records retained.")
        self.stdout.write("Record the operator, reason and this metadata in the restricted complaint record. Check archive/feed caches and any legacy static entry. Republish only through a new submitted revision and normal moderation.")

    def warn_static_archive(self, submission):
        try:
            archive = json.loads(settings.ARCHIVE_PATH.read_text(encoding="utf-8"))
            projects = archive["projects"]
            if not isinstance(projects, list) or any(not isinstance(project, dict) for project in projects):
                raise ValueError
        except (AttributeError, OSError, ValueError, KeyError, TypeError):
            self.stdout.write(self.style.WARNING("Could not check the legacy static archive; inspect it separately."))
            return
        if any(project.get("id") == submission.public_id for project in projects):
            self.stdout.write(self.style.WARNING(f"{submission.public_id} is also in docs/data/projects.json and remains public there until separately removed."))
