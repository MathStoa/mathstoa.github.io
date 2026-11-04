from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from submissions.erasure import erase_submission, has_static_listing, redact_admin_log
from submissions.forms import normalize_orcid
from submissions.models import Submission, SubmissionRevision


class Command(BaseCommand):
    help = (
        "Erase a submission with all its revisions and verification records, or remove one "
        "contributor from its draft and every revision. Prints an inventory and changes nothing without --execute."
    )

    def add_arguments(self, parser):
        parser.add_argument("submission_id", help="The submission's UUID (the hex in project-<hex> also works).")
        parser.add_argument("--remove-contributor", metavar="NAME_OR_ORCID", help="Remove only this contributor, matched by exact name or email (ignoring case) or ORCID.")
        parser.add_argument("--execute", action="store_true", help="Make the change. Without it the command is a dry run.")

    def handle(self, *args, **options):
        try:
            submission = Submission.objects.get(pk=options["submission_id"].removeprefix("project-"))
        except (Submission.DoesNotExist, ValidationError, ValueError):
            raise CommandError("No submission has that id.")
        if options["remove_contributor"] is not None:
            self.remove_contributor(submission, options["remove_contributor"], execute=options["execute"])
        else:
            self.erase(submission, execute=options["execute"])

    def inventory(self, submission):
        return {
            "revisions": submission.revisions.count(),
            "published": bool(submission.published_revision_id),
            "verification_attempts": submission.verification_attempts.count(),
            "repository_verifications": submission.repository_verifications.count(),
        }

    def erase(self, submission, *, execute):
        counts = self.inventory(submission)
        self.stdout.write(f"Submission {submission.pk} ({'published' if counts['published'] else 'not published'}): " + ", ".join(f"{name}={value}" for name, value in counts.items() if name != "published") + ".")
        self.warn_static_archive(submission)
        if not execute:
            self.stdout.write("Dry run: nothing changed. Add --execute to erase.")
            return
        redacted = erase_submission(submission)
        self.stdout.write(f"Erased submission {submission.pk} on {timezone.now().date().isoformat()}: " + ", ".join(f"{name}={value}" for name, value in counts.items() if name != "published") + f", admin_log_entries_redacted={redacted}.")
        self.stdout.write("Record this line (not the title or names) in the restricted erasure record so a backup restore can be re-applied.")

    def remove_contributor(self, submission, target, *, execute):
        target = target.strip()
        try:
            orcid = normalize_orcid(target)
        except ValidationError:
            orcid = None

        def matches(person):
            if orcid:
                return person.get("orcid") == orcid
            if "@" in target:
                return str(person.get("email", "")).strip().casefold() == target.casefold()
            return str(person.get("name", "")).strip().casefold() == target.casefold()

        revisions = list(submission.revisions.order_by("number"))
        changed_revisions = [revision for revision in revisions if any(matches(person) for person in revision.snapshot.get("collaborators", []))]
        in_draft = any(matches(person) for person in submission.collaborators)
        self.stdout.write(f"Submission {submission.pk}: contributor found in the draft: {'yes' if in_draft else 'no'}; in revisions: {[revision.number for revision in changed_revisions] or 'none'}.")
        if not in_draft and not changed_revisions:
            raise CommandError("That contributor is not listed on this submission.")
        for revision in changed_revisions:
            if all(matches(person) for person in revision.snapshot["collaborators"]):
                raise CommandError(f"Revision {revision.number} would have no contributors left. Erase the whole submission instead.")
        if submission.published_revision_id and submission.published_revision in changed_revisions:
            self.stdout.write("The published listing changes immediately; no new moderation decision is created.")
        self.warn_static_archive(submission)
        if not execute:
            self.stdout.write("Dry run: nothing changed. Add --execute to remove the contributor.")
            return
        with transaction.atomic():
            Submission.objects.filter(pk=submission.pk).update(collaborators=[person for person in submission.collaborators if not matches(person)])
            for revision in changed_revisions:
                snapshot = dict(revision.snapshot, collaborators=[person for person in revision.snapshot["collaborators"] if not matches(person)])
                # Revisions are immutable through save(); this reviewed erasure is the one exception.
                SubmissionRevision.objects.filter(pk=revision.pk).update(snapshot=snapshot)
            redacted = redact_admin_log([(SubmissionRevision, [revision.pk for revision in changed_revisions])])
        self.stdout.write(f"Removed a contributor from submission {submission.pk} on {timezone.now().date().isoformat()}: draft={'yes' if in_draft else 'no'}, revisions={len(changed_revisions)}, admin_log_entries_redacted={redacted}.")

    def warn_static_archive(self, submission):
        try:
            listed = has_static_listing(submission)
        except (AttributeError, OSError, ValueError):
            return
        if listed:
            self.stdout.write(self.style.WARNING(f"{submission.public_id} is also in docs/data/projects.json. Edit and commit that file too."))
