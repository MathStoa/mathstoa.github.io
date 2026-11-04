"""Safety withdrawal preserves records and cannot accidentally replace a listing."""
from io import StringIO
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from repository_verification.models import RepositoryVerification, VerificationAttempt
from .models import Submission, SubmissionRevision


def run(*args):
    output = StringIO()
    call_command("unpublish_submission", *args, stdout=output)
    return output.getvalue()


class UnpublishSubmissionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="withdraw-owner")
        self.submission = Submission.objects.create(
            owner=self.owner, title="Distinct private title", abstract="Original abstract",
            repository_url="https://github.com/example/project", category="math.NT",
            collaborators=[{"name": "Example, Author", "orcid": "https://orcid.org/0000-0002-1825-0097", "email": "private@example.invalid"}],
        )
        self.revision = SubmissionRevision.objects.create(
            submission=self.submission, number=1, snapshot=self.submission.snapshot(),
            submitted_by=self.owner, status="approved", reviewed_at=timezone.now(), review_note="Private review note",
        )
        Submission.objects.filter(pk=self.submission.pk).update(published_revision=self.revision)
        attempt = VerificationAttempt.objects.create(
            submission=self.submission, user=self.owner, method="github", repository_url=self.submission.repository_url,
            repository_id=1, repository_owner_id=2, full_name="example/project", token_hash="b" * 64,
            expires_at=timezone.now() + timedelta(minutes=10), consumed_at=timezone.now(),
        )
        RepositoryVerification.objects.create(
            attempt=attempt, submission=self.submission, user=self.owner, method="github", repository_url=attempt.repository_url,
            repository_id=1, repository_owner_id=2, full_name="example/project", github_user_id=3,
            github_login="owner", role="admin", valid_until=timezone.now() + timedelta(days=30),
        )

    def test_dry_run_preserves_publication_and_prints_only_metadata(self):
        output = run(self.submission.public_id)
        self.assertIn("Dry run", output)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.published_revision_id, self.revision.pk)
        for private in (self.submission.title, "private@example.invalid", self.revision.review_note):
            self.assertNotIn(private, output)

    def test_execute_removes_publication_but_preserves_private_records(self):
        before_submission = Submission.objects.values().get(pk=self.submission.pk)
        before_revision = SubmissionRevision.objects.values().get(pk=self.revision.pk)
        before_attempts = list(VerificationAttempt.objects.values())
        before_verifications = list(RepositoryVerification.objects.values())
        self.assertContains(self.client.get(reverse("archive_json")), self.submission.title)
        output = run(self.submission.public_id, "--execute", "--expected-revision", str(self.revision.pk))
        self.assertIn("Private records retained", output)
        expected = dict(before_submission, published_revision_id=None)
        self.assertEqual(Submission.objects.values().get(pk=self.submission.pk), expected)
        self.assertEqual(SubmissionRevision.objects.values().get(pk=self.revision.pk), before_revision)
        self.assertEqual(list(VerificationAttempt.objects.values()), before_attempts)
        self.assertEqual(list(RepositoryVerification.objects.values()), before_verifications)
        self.assertTrue(get_user_model().objects.filter(pk=self.owner.pk).exists())
        self.assertNotContains(self.client.get(reverse("archive_json")), self.submission.title)

    def test_stale_expected_revision_is_refused(self):
        with self.assertRaisesMessage(CommandError, "does not match"):
            run(self.submission.public_id, "--execute", "--expected-revision", str(self.revision.pk + 1))
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.published_revision_id, self.revision.pk)

    def test_static_duplicate_is_warned_and_not_modified(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            content = json.dumps({"version": 1, "projects": [{"id": self.submission.public_id}]})
            path.write_text(content)
            with override_settings(ARCHIVE_PATH=path):
                output = run(self.submission.public_id, "--execute")
            self.assertIn("remains public there", output)
            self.assertEqual(path.read_text(), content)

    def test_repeat_is_harmless_and_invalid_id_is_refused(self):
        run(self.submission.public_id, "--execute")
        self.assertIn("nothing changed", run(self.submission.public_id, "--execute"))
        with self.assertRaisesMessage(CommandError, "No submission"):
            run("not-a-submission")
