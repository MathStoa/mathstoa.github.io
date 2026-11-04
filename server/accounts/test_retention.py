from datetime import timedelta
from io import StringIO
from threading import Event, Thread
from unittest.mock import patch
import uuid

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.cache import cache
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounts import retention
from accounts.maintenance import purge_transient_data
from repository_verification.models import RepositoryVerification, VerificationAttempt
from submissions.models import Submission, SubmissionRevision


User = get_user_model()


class RetentionFixtures:
    def setUp(self):
        super().setUp()
        cache.clear()
        self.now = timezone.now()

    def user(self, *, age=1, last_login=None, verified=False, **fields):
        user = User.objects.create_user(username=uuid.uuid4().hex, **fields)
        User.objects.filter(pk=user.pk).update(date_joined=self.now - timedelta(days=age), last_login=last_login)
        user.refresh_from_db()
        if verified:
            EmailAddress.objects.create(user=user, email=f"{user.username}@example.org", verified=True, primary=True)
        return user

    def draft(self, *, age=91, owner=None):
        draft = Submission.objects.create(owner=owner or self.user(), title="Private draft", repository_url="https://github.com/example/project")
        Submission.objects.filter(pk=draft.pk).update(created_at=self.now - timedelta(days=age), updated_at=self.now - timedelta(days=age))
        draft.refresh_from_db()
        return draft

    def attempt(self, draft, *, age=100, user=None, expires_at=None):
        attempt = VerificationAttempt.objects.create(
            submission=draft, user=user or draft.owner, method="file", token_hash=uuid.uuid4().hex * 2,
            repository_url=draft.repository_url, repository_id=1, repository_owner_id=2,
            full_name="example/project", expires_at=expires_at or self.now - timedelta(days=age - 1),
        )
        VerificationAttempt.objects.filter(pk=attempt.pk).update(created_at=self.now - timedelta(days=age))
        return attempt

    def proof(self, draft, *, age=100, valid_until=None):
        attempt = self.attempt(draft, age=age)
        proof = RepositoryVerification.objects.create(
            attempt=attempt, submission=draft, user=draft.owner, method="file",
            repository_url=draft.repository_url, repository_id=1, repository_owner_id=2,
            full_name="example/project", commit_sha="a" * 40,
            valid_until=valid_until or self.now - timedelta(days=age - 30),
        )
        RepositoryVerification.objects.filter(pk=proof.pk).update(verified_at=self.now - timedelta(days=age))
        return proof

    def clean(self, *, dry_run=False):
        return retention.purge_retained_data(now=self.now, dry_run=dry_run)


class RetentionTests(RetentionFixtures, TestCase):
    def test_only_empty_never_verified_signups_expire_after_seven_days(self):
        expired = self.user(age=8)
        EmailAddress.objects.create(user=expired, email="unverified@example.org", verified=False)
        boundary = self.user(age=7)
        verified = self.user(age=8, verified=True)
        social = self.user(age=8)
        SocialAccount.objects.create(user=social, provider="github", uid="123")
        signed_in = self.user(age=8, last_login=self.now - timedelta(days=8))
        self.assertEqual(self.clean()["unverified_accounts"], 1)
        self.assertFalse(User.objects.filter(pk=expired.pk).exists())
        self.assertFalse(EmailAddress.objects.filter(user_id=expired.pk).exists())
        for user in (boundary, verified, social, signed_in):
            self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_inactive_account_uses_later_of_creation_and_last_successful_login(self):
        expired = self.user(age=400, verified=True, last_login=self.now - timedelta(days=366))
        social = self.user(age=400, last_login=self.now - timedelta(days=366))
        SocialAccount.objects.create(user=social, provider="github", uid="456")
        boundary = self.user(age=400, verified=True, last_login=self.now - timedelta(days=365))
        recent_login = self.user(age=400, last_login=self.now - timedelta(days=1))
        recent_creation = self.user(age=1, last_login=self.now - timedelta(days=400))
        self.assertEqual(self.clean()["inactive_accounts"], 2)
        self.assertFalse(User.objects.filter(pk__in=[expired.pk, social.pk]).exists())
        self.assertFalse(SocialAccount.objects.filter(user_id=social.pk).exists())
        for user in (boundary, recent_login, recent_creation):
            self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_staff_superusers_roles_and_historical_responsibilities_are_protected(self):
        staff = self.user(age=400, is_staff=True)
        superuser = self.user(age=400, is_superuser=True)
        group_user = self.user(age=400)
        group_user.groups.add(Group.objects.create(name="reviewer"))
        permission_user = self.user(age=400)
        permission_user.user_permissions.add(Permission.objects.first())
        owner = self.user(age=400)
        draft = self.draft(age=1, owner=owner)
        submitter = self.user(age=400)
        reviewer = self.user(age=400)
        SubmissionRevision.objects.create(submission=draft, number=1, snapshot=draft.snapshot(), submitted_by=submitter, reviewed_by=reviewer, status="rejected")
        verifier = self.user(age=400)
        self.attempt(draft, age=1, user=verifier, expires_at=self.now + timedelta(days=1))
        audit_user = self.user(age=400)
        LogEntry.objects.create(user=audit_user, object_repr="Historical staff action", action_flag=ADDITION)
        before = User.objects.count()
        counts = self.clean()
        self.assertEqual(counts["unverified_accounts"], 0)
        self.assertEqual(counts["inactive_accounts"], 0)
        self.assertEqual(User.objects.count(), before)

    def test_draft_boundary_and_every_submitted_status_are_preserved(self):
        expired = self.draft(age=91)
        boundary = self.draft(age=90)
        submitted = []
        for status in ("pending", "rejected", "approved"):
            draft = self.draft(age=100)
            revision = SubmissionRevision.objects.create(submission=draft, number=1, snapshot=draft.snapshot(), status=status)
            if status == "approved":
                draft.published_revision = revision
                draft.save(update_fields=["published_revision"])
            submitted.append(draft)
        self.assertEqual(self.clean()["unused_drafts"], 1)
        self.assertFalse(Submission.objects.filter(pk=expired.pk).exists())
        for draft in (boundary, *submitted):
            self.assertTrue(Submission.objects.filter(pk=draft.pk).exists())
        self.assertEqual(SubmissionRevision.objects.count(), 3)

    def test_old_expired_proof_is_removed_only_with_never_submitted_draft(self):
        expired = self.draft(age=100)
        old_proof = self.proof(expired)
        published = self.draft(age=100)
        published_proof = self.proof(published)
        revision = SubmissionRevision.objects.create(submission=published, number=1, snapshot=published.snapshot(), status="approved")
        Submission.objects.filter(pk=published.pk).update(published_revision=revision)
        counts = self.clean()
        self.assertEqual(counts["unused_drafts"], 1)
        self.assertEqual(counts["draft_verifications"], 1)
        self.assertFalse(RepositoryVerification.objects.filter(pk=old_proof.pk).exists())
        self.assertFalse(VerificationAttempt.objects.filter(pk=old_proof.attempt_id).exists())
        self.assertTrue(RepositoryVerification.objects.filter(pk=published_proof.pk).exists())
        self.assertTrue(SubmissionRevision.objects.filter(pk=revision.pk).exists())

    def test_recent_verification_and_active_attempts_preserve_old_drafts(self):
        recent_attempt = self.draft(age=100)
        self.attempt(recent_attempt, age=5)
        recent_proof = self.draft(age=100)
        self.proof(recent_proof, age=40)
        active_attempt = self.draft(age=100)
        self.attempt(active_attempt, age=100, expires_at=self.now + timedelta(days=1))
        active_proof = self.draft(age=100)
        self.proof(active_proof, age=100, valid_until=self.now + timedelta(days=1))
        self.assertEqual(self.clean()["unused_drafts"], 0)
        self.assertEqual(Submission.objects.count(), 4)

    def test_recent_activity_survives_pruning_its_transient_record(self):
        draft = self.draft(age=100)
        attempt = self.attempt(draft, age=100)
        VerificationAttempt.objects.filter(pk=attempt.pk).update(revoked_at=self.now - timedelta(hours=1))
        with patch("accounts.maintenance.timezone.now", return_value=self.now):
            counts = purge_transient_data()
        self.assertEqual(counts["unused_drafts"], 0)
        self.assertFalse(VerificationAttempt.objects.filter(pk=attempt.pk).exists())
        draft.refresh_from_db()
        self.assertEqual(draft.updated_at, self.now - timedelta(hours=1))
        counts = retention.purge_retained_data(now=self.now + timedelta(days=1))
        self.assertEqual(counts["unused_drafts"], 0)

    def test_dry_run_reports_retention_without_deleting_or_updating_content(self):
        unverified = self.user(age=8)
        inactive = self.user(age=400, verified=True)
        draft = self.draft(age=100)
        proof = self.proof(draft)
        active = self.draft(age=100)
        self.attempt(active, age=1)
        output = StringIO()
        with patch("accounts.maintenance.timezone.now", return_value=self.now):
            call_command("purge_transient_data", dry_run=True, stdout=output)
        for count in ("unverified_accounts=1", "inactive_accounts=1", "unused_drafts=1", "draft_verifications=1"):
            self.assertIn(count, output.getvalue())
        self.assertEqual(User.objects.filter(pk__in=[unverified.pk, inactive.pk]).count(), 2)
        self.assertTrue(Submission.objects.filter(pk=draft.pk).exists())
        self.assertTrue(RepositoryVerification.objects.filter(pk=proof.pk).exists())
        active.refresh_from_db()
        self.assertEqual(active.updated_at, self.now - timedelta(days=100))

    def test_account_and_draft_are_rechecked_after_candidate_selection(self):
        user = self.user(age=400, verified=True)
        draft = self.draft(age=100)
        original_account = retention._purge_account
        original_draft = retention._purge_draft
        def login_then_check(pk, **kwargs):
            User.objects.filter(pk=pk).update(last_login=self.now)
            return original_account(pk, **kwargs)
        def submit_then_check(pk, **kwargs):
            SubmissionRevision.objects.create(submission=draft, number=1, snapshot=draft.snapshot(), status="pending")
            return original_draft(pk, **kwargs)
        with patch("accounts.retention._purge_account", side_effect=login_then_check), patch("accounts.retention._purge_draft", side_effect=submit_then_check):
            counts = self.clean()
        self.assertEqual(counts["inactive_accounts"], 0)
        self.assertEqual(counts["unused_drafts"], 0)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertTrue(Submission.objects.filter(pk=draft.pk).exists())

    def test_stale_session_cannot_restore_a_deleted_inactive_account(self):
        user = self.user(age=400, verified=True)
        self.client.force_login(user)
        User.objects.filter(pk=user.pk).update(last_login=self.now - timedelta(days=366))
        self.assertEqual(self.clean()["inactive_accounts"], 1)
        response = self.client.get("/submissions/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_privacy_and_registration_notices_are_visible(self):
        self.assertContains(self.client.get("/privacy/"), "90 days")
        self.assertContains(self.client.get("/accounts/login/"), 'href="/privacy/"')
        self.assertContains(self.client.get("/accounts/signup/"), "seven days")
        self.assertContains(self.client.get("/accounts/github/login/"), "365 days")


class PostgreSQLRetentionRaceTests(RetentionFixtures, TransactionTestCase):
    def with_concurrent_update(self, write, check):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row locks; exercised by PostgreSQL CI")
        locked, release = Event(), Event()
        errors = []
        def editing():
            close_old_connections()
            try:
                with transaction.atomic():
                    write()
                    locked.set()
                    if not release.wait(5):
                        raise RuntimeError("Editor test was not released")
            except BaseException as error:
                errors.append(error)
                locked.set()
            finally:
                close_old_connections()
        thread = Thread(target=editing)
        thread.start()
        try:
            self.assertTrue(locked.wait(5))
            self.assertFalse(errors)
            check()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)

    def test_cleanup_skips_a_draft_locked_by_an_editor(self):
        draft = self.draft(age=100)
        def write():
            record = Submission.objects.select_for_update().get(pk=draft.pk)
            record.title = "Current work"
            record.save()
        self.with_concurrent_update(write, lambda: self.assertEqual(self.clean()["unused_drafts"], 0))
        self.assertEqual(Submission.objects.get(pk=draft.pk).title, "Current work")

    def test_cleanup_skips_an_account_with_a_login_update_in_progress(self):
        user = self.user(age=400, verified=True)
        def write():
            record = User.objects.select_for_update().get(pk=user.pk)
            record.last_login = self.now
            record.save(update_fields=["last_login"])
        self.with_concurrent_update(write, lambda: self.assertEqual(self.clean()["inactive_accounts"], 0))
        self.assertEqual(User.objects.get(pk=user.pk).last_login, self.now)
