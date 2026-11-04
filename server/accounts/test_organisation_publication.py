"""Organisation approval remains required throughout repository publication."""
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from repository_verification.github import FileEvidence, PermissionEvidence, Repository
from repository_verification.models import RepositoryVerification
from repository_verification.services import (
    VerificationError, challenge_document, check_file, check_github,
    require_verified, start_attempt,
)
from submissions.models import Submission, SubmissionRevision
from submissions.publication import accepted_declaration

from .models import OrganisationAccount


class OrganisationPublicationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="organisation-publisher", email="team@example.org")
        self.organisation = OrganisationAccount.objects.create(
            user=self.user, name="Test Research Group", email=self.user.email,
            website="https://example.org", description="An independent research group.",
            representative_name="Example Representative", authority="I run the group.", status="approved",
        )
        self.moderator = get_user_model().objects.create_superuser(username="organisation-reviewer", email="moderator@example.org", password=None)
        self.submission = Submission.objects.create(
            owner=self.user, title="Organisation research", repository_url="https://github.com/research/project",
            abstract="These research notes explore a mathematical question, explain the current partial results, and describe useful future work.",
            category="math.PR", collaborators=[{"name": "Anna, Example", "email": "anna@example.org"}],
            permission_confirmed=True, license_confirmed=True,
        )
        self.revision = SubmissionRevision.objects.create(
            submission=self.submission, number=1, submitted_by=self.user, status="unverified",
            snapshot={**self.submission.snapshot(), "publication_declaration": accepted_declaration()},
        )
        self.repo = Repository(101, 201, "Organization", "research", "project", "main")
        self.api = Mock()
        self.api.resolve_repository.return_value = self.repo
        self.api.verify_user_access.return_value = PermissionEvidence(self.repo, 301, "representative", "admin")

    def state(self, status, active=True):
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(status=status)
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=active)
        # Deliberately leave self.user and self.submission.owner stale.

    def issue(self, method="file"):
        attempt, token = start_attempt(self.submission, self.user, method, client=self.api)
        if method == "file":
            self.api.read_verification_file.return_value = FileEvidence(
                self.repo, "a" * 40, "b" * 40, challenge_document(attempt, token),
            )
        return attempt, token

    def verify(self):
        attempt, token = self.issue()
        return check_file(attempt, self.user, client=self.api)

    def test_approved_organisation_verifies_and_is_published_after_project_review(self):
        proof = self.verify()
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "pending")
        self.assertEqual(require_verified(self.submission, client=self.api), proof)
        with patch("repository_verification.services._client", return_value=self.api):
            self.revision.review(self.moderator, approve=True)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.published_revision_id, self.revision.pk)

    def test_pending_rejected_and_deactivated_cannot_start_verification(self):
        for status, active in (("pending", True), ("rejected", True), ("approved", False)):
            with self.subTest(status=status, active=active):
                self.state(status, active)
                with self.assertRaisesRegex(VerificationError, "approved and active"):
                    self.issue()
        self.api.resolve_repository.assert_not_called()
        self.assertFalse(self.submission.verification_attempts.exists())

    def test_pending_rejected_and_deactivated_cannot_finish_either_verification_method(self):
        for method in ("file", "github"):
            for status, active in (("pending", True), ("rejected", True), ("approved", False)):
                with self.subTest(method=method, status=status, active=active):
                    self.state("approved")
                    attempt, token = self.issue(method)
                    self.api.reset_mock()
                    self.state(status, active)
                    with self.assertRaisesRegex(VerificationError, "approved and active"):
                        if method == "file":
                            check_file(attempt, self.user, client=self.api)
                        else:
                            check_github(attempt, self.user, token, "transient-token", "301", client=self.api)
                    self.api.resolve_repository.assert_not_called()
                    attempt.refresh_from_db()
                    self.assertIsNone(attempt.consumed_at)
        self.assertFalse(RepositoryVerification.objects.exists())
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "unverified")

    def test_revocation_during_file_fetch_blocks_proof_creation(self):
        attempt, token = self.issue()
        evidence = self.api.read_verification_file.return_value

        def revoke(_):
            self.state("rejected")
            return evidence

        self.api.read_verification_file.side_effect = revoke
        with self.assertRaisesRegex(VerificationError, "approved and active"):
            check_file(attempt, self.user, client=self.api)
        self.assertFalse(RepositoryVerification.objects.exists())
        attempt.refresh_from_db()
        self.assertIsNone(attempt.consumed_at)

    def test_pending_rejected_and_deactivated_cannot_publish_an_existing_proof(self):
        self.verify()
        self.revision.refresh_from_db()
        self.api.reset_mock()
        for status, active in (("pending", True), ("rejected", True), ("approved", False)):
            with self.subTest(status=status, active=active):
                self.state(status, active)
                with self.assertRaisesRegex(VerificationError, "approved and active"):
                    require_verified(self.submission, client=self.api)
                with patch("repository_verification.services._client", return_value=self.api):
                    with self.assertRaisesRegex(VerificationError, "approved and active"):
                        self.revision.review(self.moderator, approve=True)
        self.api.resolve_repository.assert_not_called()
        self.revision.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual(self.revision.status, "pending")
        self.assertIsNone(self.submission.published_revision_id)

    def test_deactivation_during_publication_recheck_blocks_approval(self):
        self.verify()
        self.revision.refresh_from_db()

        def deactivate(_):
            self.state("approved", active=False)
            return self.repo

        self.api.resolve_repository.side_effect = deactivate
        with patch("repository_verification.services._client", return_value=self.api):
            with self.assertRaisesRegex(VerificationError, "approved and active"):
                self.revision.review(self.moderator, approve=True)
        self.submission.refresh_from_db()
        self.assertIsNone(self.submission.published_revision_id)
