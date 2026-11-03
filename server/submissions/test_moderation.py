"""Moderation aids: review-queue count, self-approval guard, admin evidence and notices."""
import sys
from datetime import timedelta
from types import ModuleType
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from repository_verification.models import RepositoryVerification, VerificationAttempt
from .admin import SubmissionRevisionAdmin
from .models import Submission, SubmissionRevision
from .notifications import notify_contributors, notify_moderators


NOTIFY = {"MODERATION_NOTIFY_EMAIL": "reviewers@example.invalid", "DEFAULT_FROM_EMAIL": "mathStoa <noreply@example.invalid>"}


def reviewer(username):
    return get_user_model().objects.create_superuser(username=username, email=f"{username}@example.invalid", password="test-only")


def revision_for(submission, submitted_by=None):
    return SubmissionRevision.objects.create(submission=submission, number=submission.revision, snapshot=submission.snapshot(), submitted_by=submitted_by)


class ReviewQueueTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="queue-owner")
        self.reviewer = reviewer("queue-reviewer")
        submission = Submission.objects.create(owner=self.owner, title="Queued project", repository_url="https://github.com/example/queued")
        revision_for(submission, self.owner)
        self.queue_link = reverse("admin:submissions_submissionrevision_changelist") + "?status__exact=pending"

    def test_reviewers_see_the_pending_count_in_the_header(self):
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("submissions:list"))
        self.assertContains(response, "Review queue (1)")
        self.assertContains(response, self.queue_link)
        SubmissionRevision.objects.update(status="rejected")
        response = self.client.get(reverse("submissions:list"))
        self.assertContains(response, "Review queue</a>")
        self.assertNotContains(response, "Review queue (")

    def test_other_users_do_not_see_the_queue(self):
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("submissions:list")), "Review queue")
        staff_without_permission = get_user_model().objects.create_user(username="queue-staff", is_staff=True)
        self.client.force_login(staff_without_permission)
        self.assertNotContains(self.client.get(reverse("submissions:list")), "Review queue")
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("account_login")), "Review queue")


class SelfReviewTests(TestCase):
    def setUp(self):
        self.owner = reviewer("owner-reviewer")
        self.editor = reviewer("editor-reviewer")
        self.former_editor = reviewer("former-editor")
        self.submission = Submission.objects.create(owner=self.owner, title="Own project", repository_url="https://github.com/example/own")
        self.submission.editors.add(self.editor)
        self.revision = revision_for(self.submission, submitted_by=self.former_editor)

    def test_owner_editor_and_submitter_cannot_approve(self):
        for user in (self.owner, self.editor, self.former_editor):
            with self.subTest(user=user.username), self.assertRaisesRegex(ValidationError, "own, edit or submitted"):
                self.revision.review(user, approve=True)
        self.revision.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual(self.revision.status, "pending")
        self.assertIsNone(self.submission.published_revision)

    def test_involved_reviewer_can_still_request_changes(self):
        self.revision.review(self.owner, approve=False, note="Withdrawn by the owner.")
        self.assertEqual(self.revision.status, "rejected")
        self.assertEqual(self.revision.reviewed_by, self.owner)


class AdminEvidenceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="evidence-owner")
        self.submission = Submission.objects.create(owner=self.owner, title="Evidence project", repository_url="https://github.com/owner/research")
        self.revision = revision_for(self.submission, self.owner)
        self.admin = SubmissionRevisionAdmin(SubmissionRevision, AdminSite())

    def proof(self, **extra):
        attempt = VerificationAttempt.objects.create(
            submission=self.submission, user=self.owner, method="github", repository_url=self.submission.repository_url,
            repository_id=123, repository_owner_id=456, full_name="owner/research", token_hash="a" * 64,
            expires_at=timezone.now() + timedelta(minutes=10), consumed_at=timezone.now(),
        )
        values = dict(
            attempt=attempt, submission=self.submission, user=self.owner, method="github",
            repository_url=self.submission.repository_url, repository_id=123, repository_owner_id=456,
            full_name="owner/research", github_user_id=42, github_login="researcher", role="owner",
            valid_until=timezone.now() + timedelta(days=30),
        )
        values.update(extra)
        return RepositoryVerification.objects.create(**values)

    def test_missing_proof_is_stated(self):
        self.assertIn("No repository verification", str(self.admin.verification_display(self.revision)))
        self.assertIn("verification_display", self.admin.get_readonly_fields(None, self.revision))

    def test_current_github_proof_is_summarised(self):
        self.proof()
        text = str(self.admin.verification_display(self.revision))
        for expected in ("GitHub authorization by researcher", "role: owner", "https://github.com/owner/research", "Status: current"):
            self.assertIn(expected, text)
        self.assertNotIn("Warning", text)

    def test_expired_or_moved_proof_is_flagged(self):
        self.proof(valid_until=timezone.now() - timedelta(seconds=1))
        self.assertIn("expired or revoked", str(self.admin.verification_display(self.revision)))
        self.revision.snapshot["repository_url"] = "https://github.com/owner/other"
        self.assertIn("different repository URL", str(self.admin.verification_display(self.revision)))

    def test_completed_proofs_are_listed_read_only(self):
        self.assertIn(RepositoryVerification, admin.site._registry)
        self.assertNotIn(VerificationAttempt, admin.site._registry)
        verification_admin = admin.site._registry[RepositoryVerification]
        self.assertFalse(verification_admin.has_change_permission(Mock()))
        self.assertFalse(verification_admin.has_delete_permission(Mock()))
        self.assertNotIn("token_hash", verification_admin.readonly_fields)


class SubmitNoticeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username="notice-owner")
        self.submission = Submission.objects.create(
            owner=self.owner, title="Notice  test\nproject", repository_url="https://github.com/example/research",
            permission_confirmed=True, license_confirmed=True,
        )
        # Ownership proof and the full form have their own tests; isolate the moderation path.
        verification = ModuleType("repository_verification.services")
        verification.require_verified = Mock(return_value=None)
        verification.has_current_verification = Mock(return_value=True)
        for patcher in (patch.dict(sys.modules, {"repository_verification.services": verification}),
                        patch("submissions.views.review_form", return_value=Mock(**{"is_valid.return_value": True}))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client.force_login(self.owner)

    def submit(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("submissions:submit", args=[self.submission.pk]), {"revision": self.submission.revision}, follow=True)
        self.assertEqual(response.status_code, 200)
        return response

    @override_settings(**NOTIFY, PUBLIC_CONTACT_EMAIL="contact@example.invalid")
    def test_submitting_a_revision_emails_the_moderation_mailbox(self):
        response = self.submit()
        self.assertContains(response, "usually within a week")
        self.assertContains(response, "contact@example.invalid")
        revision = self.submission.revisions.get()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["reviewers@example.invalid"])
        self.assertEqual(message.subject, f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Submission awaiting review: Notice test project")
        self.assertIn(reverse("admin:submissions_submissionrevision_change", args=[revision.pk]), message.body)
        self.assertIn("status__exact=pending", message.body)
        for private in ("notice-owner", str(self.submission.pk), self.submission.pk.hex):
            self.assertNotIn(private, message.body)

    def test_without_a_mailbox_nothing_is_sent(self):
        with override_settings(MODERATION_NOTIFY_EMAIL=""):
            self.submit()
        self.assertEqual(mail.outbox, [])
        self.assertTrue(self.submission.revisions.filter(status="pending").exists())

    @override_settings(**NOTIFY)
    def test_mail_failure_never_blocks_the_submission(self):
        with patch("submissions.notifications.send_mail", side_effect=OSError("smtp down")):
            with self.assertLogs("submissions.notifications", level="WARNING"):
                self.submit()
        self.assertTrue(self.submission.revisions.filter(status="pending").exists())
        self.assertEqual(mail.outbox, [])

    @override_settings(**NOTIFY)
    def test_notice_is_sent_once_per_revision_and_not_for_repeats(self):
        self.submit()
        self.submit()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.submission.revisions.count(), 1)

    @override_settings(**NOTIFY, EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="")
    def test_production_without_smtp_skips_the_notice(self):
        revision = revision_for(self.submission, self.owner)
        self.assertFalse(notify_moderators(revision))
        self.assertEqual(mail.outbox, [])


class ContributorConfirmationTests(TestCase):
    submit = SubmitNoticeTests.submit

    def setUp(self):
        SubmitNoticeTests.setUp(self)
        self.submission.collaborators = [
            {"name": "Anna, Example", "orcid": "https://orcid.org/0000-0002-1825-0097", "email": "anna@university.example"},
            {"name": "Ben, Example", "orcid": "", "email": "ben@institute.example"},
        ]
        self.submission.save()

    @override_settings(**NOTIFY, PUBLIC_CONTACT_EMAIL="contact@example.invalid")
    def test_each_contributor_gets_their_own_confirmation(self):
        self.submit()
        confirmations = [message for message in mail.outbox if message.to != ["reviewers@example.invalid"]]
        self.assertEqual(sorted(message.to[0] for message in confirmations), ["anna@university.example", "ben@institute.example"])
        for message in confirmations:
            self.assertEqual(len(message.to), 1)
            self.assertEqual(message.cc + message.bcc, [])
            self.assertEqual(message.reply_to, ["contact@example.invalid"])
            self.assertEqual(message.subject, f'{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}You are listed as a contributor to "Notice test project"')
            self.assertIn("- Anna Example\n- Ben Example", message.body)
            self.assertIn("/privacy/", message.body)
            # No contributor learns another's address, and nothing names the submitting account.
            other = "ben@institute.example" if message.to == ["anna@university.example"] else "anna@university.example"
            for private in (other, "notice-owner"):
                self.assertNotIn(private, message.body)

    @override_settings(**NOTIFY)
    def test_confirmations_are_not_repeated(self):
        self.submit()
        self.submit()
        self.assertEqual(len(mail.outbox), 3)

    @override_settings(**NOTIFY, EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="")
    def test_production_without_smtp_sends_no_confirmation(self):
        self.assertEqual(notify_contributors(revision_for(self.submission, self.owner)), 0)
        self.assertEqual(mail.outbox, [])

    @override_settings(**NOTIFY)
    def test_a_failed_confirmation_never_blocks_the_submission(self):
        with patch("submissions.notifications.EmailMessage.send", side_effect=OSError("smtp down")):
            with self.assertLogs("submissions.notifications", level="WARNING"):
                self.submit()
        self.assertTrue(self.submission.revisions.filter(status="pending").exists())
