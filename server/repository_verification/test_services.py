import json
from io import StringIO
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock, patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from submissions.models import Submission
from .github import FileEvidence, GitHubError, PermissionEvidence, Repository
from .models import RepositoryVerification, VerificationAttempt
from .services import (VerificationError, challenge_document, check_file, check_github,
                       has_current_verification, require_verified, start_attempt)


class VerificationServicesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="author")
        self.other = get_user_model().objects.create_user(username="other")
        self.orcid = SocialAccount.objects.create(user=self.user, provider="orcid", uid="0000-0002-1825-0097")
        self.submission = Submission.objects.create(owner=self.user, title="Research", repository_url="https://github.com/research/project")
        self.repo = Repository(101, 201, "User", "research", "project", "main")
        self.api = Mock()
        self.api.resolve_repository.return_value = self.repo
        self.api.verify_user_access.return_value = PermissionEvidence(self.repo, 201, "research", "owner")

    def issue(self, method="file"):
        return start_attempt(self.submission, self.user, method, client=self.api)

    def file_evidence(self, attempt, raw_token, **updates):
        content = json.loads(challenge_document(attempt, raw_token))
        content.update(updates)
        self.api.read_verification_file.return_value = FileEvidence(self.repo, "a" * 40, "b" * 40, json.dumps(content))

    def verify_file(self):
        attempt, token = self.issue()
        self.file_evidence(attempt, token)
        return check_file(attempt, self.user, client=self.api)

    def test_file_happy_path_and_evidence(self):
        proof = self.verify_file()
        self.assertEqual(proof.commit_sha, "a" * 40)
        self.assertEqual(proof.orcid, self.orcid.uid)
        self.assertEqual(proof.repository_id, 101)
        self.assertEqual(proof.repository_owner_id, 201)
        self.assertEqual(proof.role, "endorsement")
        self.assertTrue(has_current_verification(self.submission))
        self.assertEqual(require_verified(self.submission, client=self.api), proof)

    def test_token_only_stored_as_digest(self):
        attempt, token = self.issue()
        self.assertNotIn(token, repr(attempt.__dict__))
        self.assertNotEqual(attempt.token_hash, token)
        self.assertEqual(len(attempt.token_hash), 64)

    def test_editor_cannot_issue_owner_claim(self):
        self.submission.editors.add(self.other)
        with self.assertRaises(VerificationError):
            start_attempt(self.submission, self.other, "file", client=self.api)
        self.api.resolve_repository.assert_not_called()

    def test_wrong_account_cannot_complete(self):
        attempt, token = self.issue()
        self.file_evidence(attempt, token)
        with self.assertRaises(VerificationError):
            check_file(attempt, self.other, client=self.api)
        self.api.read_verification_file.assert_not_called()

    def test_expired_challenge_rejected(self):
        attempt, _ = self.issue()
        VerificationAttempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(VerificationError):
            check_file(attempt, self.user, client=self.api)

    def test_reissuing_revokes_previous_challenge(self):
        attempt, token = self.issue()
        self.issue("github")
        self.file_evidence(attempt, token)
        with self.assertRaises(VerificationError):
            check_file(attempt, self.user, client=self.api)

    def test_replay_rejected_after_success(self):
        proof = self.verify_file()
        with self.assertRaises(VerificationError):
            check_file(proof.attempt, self.user, client=self.api)
        self.assertEqual(RepositoryVerification.objects.count(), 1)

    def test_wrong_token_or_account_document_rejected(self):
        attempt, token = self.issue()
        for update in ({"token": "a-different-token"}, {"orcid": "somebody-else"},
                       {"submission": str(self.other.pk)}, {"repository_id": 102},
                       {"authorization": "I do not authorize this."}):
            with self.subTest(update=update):
                self.file_evidence(attempt, token, **update)
                with self.assertRaises(VerificationError):
                    check_file(attempt, self.user, client=self.api)
        self.assertFalse(RepositoryVerification.objects.exists())

    def test_malformed_file_rejected(self):
        attempt, _ = self.issue()
        for content in ("not-json", "[]", "null"):
            with self.subTest(content=content):
                self.api.read_verification_file.return_value = FileEvidence(self.repo, "a" * 40, "b" * 40, content)
                with self.assertRaises(VerificationError):
                    check_file(attempt, self.user, client=self.api)

    def test_fork_or_replacement_repo_cannot_satisfy_challenge(self):
        attempt, token = self.issue()
        self.file_evidence(attempt, token)
        self.api.resolve_repository.return_value = replace(self.repo, repository_id=999)
        with self.assertRaises(VerificationError):
            check_file(attempt, self.user, client=self.api)
        self.api.read_verification_file.assert_not_called()

    def test_transfer_during_fetch_rejected(self):
        attempt, token = self.issue()
        self.file_evidence(attempt, token)
        self.api.resolve_repository.side_effect = [self.repo, replace(self.repo, owner_id=999)]
        with self.assertRaises(VerificationError):
            check_file(attempt, self.user, client=self.api)
        self.assertFalse(RepositoryVerification.objects.exists())

    def test_repo_change_during_fetch_rejected(self):
        attempt, token = self.issue()
        self.file_evidence(attempt, token)
        evidence = self.api.read_verification_file.return_value
        def change_repository(_):
            Submission.objects.filter(pk=self.submission.pk).update(repository_url="https://github.com/another/project")
            return evidence
        self.api.read_verification_file.side_effect = change_repository
        with self.assertRaises(VerificationError):
            check_file(attempt, self.user, client=self.api)

    def test_orcid_change_invalidates_pending_proof(self):
        attempt, token = self.issue()
        self.file_evidence(attempt, token)
        SocialAccount.objects.filter(pk=self.orcid.pk).update(uid="0000-0001-5109-3700")
        with self.assertRaises(VerificationError):
            check_file(attempt, self.user, client=self.api)

    def test_github_happy_path_without_token_persistence(self):
        attempt, nonce = self.issue("github")
        proof = check_github(attempt, self.user, nonce, "transient-github-secret", "201", client=self.api)
        self.assertEqual(proof.github_user_id, 201)
        self.assertEqual(proof.github_login, "research")
        self.assertEqual(proof.role, "owner")
        self.assertNotIn("transient-github-secret", repr(proof.__dict__))
        self.assertFalse(SocialAccount.objects.filter(user=self.user, provider="github").exists())

    def test_github_nonce_or_identity_mismatch_rejected(self):
        attempt, nonce = self.issue("github")
        for code, uid in (("bad", "201"), (nonce, "999"), (nonce, None)):
            with self.subTest(code=code, uid=uid):
                with self.assertRaises(VerificationError):
                    check_github(attempt, self.user, code, "secret", uid, client=self.api)
        self.assertFalse(RepositoryVerification.objects.exists())

    def test_github_already_linked_to_other_user_rejected(self):
        SocialAccount.objects.create(user=self.other, provider="github", uid="201")
        attempt, nonce = self.issue("github")
        with self.assertRaises(VerificationError):
            check_github(attempt, self.user, nonce, "secret", "201", client=self.api)

    def test_gate_requires_proof(self):
        self.assertFalse(has_current_verification(self.submission))
        with self.assertRaises(VerificationError):
            require_verified(self.submission, client=self.api)
        self.api.resolve_repository.assert_not_called()

    def test_gate_rechecks_immutable_repo_id_and_owner(self):
        proof = self.verify_file()
        self.api.resolve_repository.return_value = replace(self.repo, owner_id=999)
        with self.assertRaises(VerificationError):
            require_verified(self.submission, client=self.api)
        proof.refresh_from_db()
        self.assertIsNotNone(proof.revoked_at)

    def test_gate_outage_recoverable_no_revocation(self):
        proof = self.verify_file()
        self.api.resolve_repository.side_effect = GitHubError("github_unavailable", "GitHub is unavailable. Please try again later.")
        with self.assertRaisesMessage(VerificationError, "GitHub is unavailable"):
            require_verified(self.submission, client=self.api)
        proof.refresh_from_db()
        self.assertIsNone(proof.revoked_at)

    def test_expired_proof_rejected(self):
        proof = self.verify_file()
        RepositoryVerification.objects.filter(pk=proof.pk).update(valid_until=timezone.now() - timedelta(seconds=1))
        self.assertFalse(has_current_verification(self.submission))
        with self.assertRaises(VerificationError):
            require_verified(self.submission, client=self.api)

    def test_repo_change_and_revert_does_not_restore_proof(self):
        proof = self.verify_file()
        original = self.submission.repository_url
        self.submission.repository_url = "https://github.com/another/project"
        self.submission.save()
        self.submission.repository_url = original
        self.submission.save()
        self.assertFalse(has_current_verification(self.submission))
        proof.refresh_from_db()
        self.assertIsNotNone(proof.revoked_at)

    def test_completion_rolls_back_token_consume_if_proof_save_fails(self):
        attempt, token = self.issue()
        self.file_evidence(attempt, token)
        with patch.object(RepositoryVerification.objects, "create", side_effect=RuntimeError("database failed")):
            with self.assertRaises(RuntimeError):
                check_file(attempt, self.user, client=self.api)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.consumed_at)

    @override_settings(REPOSITORY_GITHUB_READ_TOKEN="server-public-read-credential")
    def test_public_read_credential_passed_only_to_client(self):
        with patch("repository_verification.services.GitHubClient", return_value=self.api) as client_factory:
            attempt, _ = start_attempt(self.submission, self.user, "file")
        client_factory.assert_called_once_with(public_token="server-public-read-credential")
        self.assertNotIn("server-public-read-credential", repr(attempt.__dict__))

    def test_cleanup_preserves_proof_and_active_attempts(self):
        proof = self.verify_file()
        old = timezone.now() - timedelta(days=40)
        VerificationAttempt.objects.filter(pk=proof.attempt_id).update(created_at=old, expires_at=old)
        expired, _ = self.issue()
        VerificationAttempt.objects.filter(pk=expired.pk).update(created_at=old, expires_at=old)
        active, _ = self.issue()
        call_command("purge_repository_verification_attempts", stdout=StringIO())
        self.assertTrue(VerificationAttempt.objects.filter(pk=proof.attempt_id).exists())
        self.assertTrue(RepositoryVerification.objects.filter(pk=proof.pk).exists())
        self.assertTrue(VerificationAttempt.objects.filter(pk=active.pk).exists())
        self.assertFalse(VerificationAttempt.objects.filter(pk=expired.pk).exists())
