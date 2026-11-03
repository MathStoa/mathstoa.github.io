"""The erase_submission command: dry run, whole-submission erasure and contributor removal."""
from datetime import timedelta
from io import StringIO

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from repository_verification.models import RepositoryVerification, VerificationAttempt
from .models import EditorInvitation, Submission, SubmissionRevision


ADA = {"name": "Ada Example", "orcid": "https://orcid.org/0000-0002-1825-0097"}
BEN = {"name": "Ben Example", "orcid": "", "email": "ben@university.example"}


def run(*args):
    out = StringIO()
    call_command("erase_submission", *args, stdout=out)
    return out.getvalue()


class EraseSubmissionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="erase-owner")
        self.reviewer = User.objects.create_superuser(username="erase-reviewer", email="r@example.invalid", password="test-only")
        self.submission = Submission.objects.create(
            owner=self.owner, title="Private research", repository_url="https://github.com/example/research", category="math.NT", collaborators=[ADA, BEN],
        )
        self.submission.editors.add(User.objects.create_user(username="erase-editor"))
        self.revision = SubmissionRevision.objects.create(
            submission=self.submission, number=1, snapshot=self.submission.snapshot(), submitted_by=self.owner,
            status="approved", reviewed_by=self.reviewer, reviewed_at=timezone.now(),
        )
        Submission.objects.filter(pk=self.submission.pk).update(published_revision=self.revision)
        EditorInvitation.issue(self.submission)
        attempt = VerificationAttempt.objects.create(
            submission=self.submission, user=self.owner, method="github", repository_url=self.submission.repository_url,
            repository_id=1, repository_owner_id=2, full_name="example/research", token_hash="b" * 64,
            expires_at=timezone.now() + timedelta(minutes=10), consumed_at=timezone.now(),
        )
        RepositoryVerification.objects.create(
            attempt=attempt, submission=self.submission, user=self.owner, method="github", repository_url=attempt.repository_url,
            repository_id=1, repository_owner_id=2, full_name="example/research", github_user_id=3, github_login="owner",
            role="admin", valid_until=timezone.now() + timedelta(days=30),
        )
        LogEntry.objects.log_action(
            user_id=self.reviewer.pk, content_type_id=ContentType.objects.get_for_model(SubmissionRevision).pk,
            object_id=self.revision.pk, object_repr=str(self.revision), action_flag=CHANGE, change_message="Changed review note.",
        )

    def test_dry_run_changes_nothing(self):
        output = run(str(self.submission.pk))
        self.assertIn("revisions=1", output)
        self.assertIn("repository_verifications=1", output)
        self.assertIn("Dry run", output)
        self.assertTrue(Submission.objects.filter(pk=self.submission.pk).exists())

    def test_execute_erases_everything_and_redacts_the_admin_log(self):
        self.assertContains(self.client.get(reverse("archive_json")), "Private research")
        output = run(self.submission.public_id, "--execute")
        self.assertIn("admin_log_entries_redacted=1", output)
        self.assertNotIn("Private research", output)
        self.assertFalse(Submission.objects.exists())
        self.assertFalse(SubmissionRevision.objects.exists())
        self.assertFalse(EditorInvitation.objects.exists())
        self.assertFalse(VerificationAttempt.objects.exists())
        self.assertFalse(RepositoryVerification.objects.exists())
        entry = LogEntry.objects.get()
        self.assertEqual((entry.object_repr, entry.change_message), ("[erased]", ""))
        self.assertNotContains(self.client.get(reverse("archive_json")), "Private research")
        # Accounts are untouched; closing them is a separate request.
        self.assertTrue(get_user_model().objects.filter(pk=self.owner.pk).exists())

    def test_unknown_id_is_refused(self):
        with self.assertRaises(CommandError):
            run("not-a-uuid")

    def test_contributor_removed_by_orcid_from_draft_and_published_revision(self):
        output = run(str(self.submission.pk), "--remove-contributor", "0000-0002-1825-0097", "--execute")
        self.assertIn("revisions=1", output)
        self.submission.refresh_from_db()
        self.revision.refresh_from_db()
        self.assertEqual(self.submission.collaborators, [BEN])
        self.assertEqual(self.revision.snapshot["collaborators"], [BEN])
        self.assertEqual(LogEntry.objects.get().object_repr, "[erased]")
        archive = self.client.get(reverse("archive_json")).content.decode()
        self.assertNotIn("Ada Example", archive)
        self.assertIn("Ben Example", archive)

    def test_contributor_matched_by_name_and_dry_run_by_default(self):
        output = run(str(self.submission.pk), "--remove-contributor", "ben example")
        self.assertIn("Dry run", output)
        self.submission.refresh_from_db()
        self.assertIn(BEN, self.submission.collaborators)

    def test_contributor_matched_by_email(self):
        run(str(self.submission.pk), "--remove-contributor", "Ben@University.example", "--execute")
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.collaborators, [ADA])

    def test_removing_the_last_contributor_is_refused(self):
        run(str(self.submission.pk), "--remove-contributor", "Ben Example", "--execute")
        with self.assertRaisesMessage(CommandError, "Erase the whole submission"):
            run(str(self.submission.pk), "--remove-contributor", "Ada Example", "--execute")

    def test_unlisted_contributor_is_refused(self):
        with self.assertRaises(CommandError):
            run(str(self.submission.pk), "--remove-contributor", "Nobody")
