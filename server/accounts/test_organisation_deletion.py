"""Account deletion must remove the entire login without erasing research history."""
from datetime import timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress, Login
from allauth.account.internal.flows.login import record_authentication
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from allauth.socialaccount.models import SocialAccount
from django.contrib.admin.models import ADDITION, DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from repository_verification.models import RepositoryVerification, VerificationAttempt
from submissions.models import Submission, SubmissionRevision

from .models import OrganisationAccount
from .organisation_deletion import can_delete_organisation, delete_organisation, organisation_deletion_blockers
from .organisation_notifications import process_organisation_notifications
from .sessions import SessionStore


class OrganisationDeletionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.moderator = User.objects.create_user(username="deletion-moderator", is_staff=True)
        self.permissions = list(Permission.objects.filter(content_type__app_label="accounts", codename__in=("change_organisationaccount", "delete_organisationaccount")))
        self.moderator.user_permissions.add(*self.permissions)
        self.other = User.objects.create_user(username="other-account", email="other@example.org")
        # allauth's pending login key uses base36, unlike Django's decimal ID.
        self.user = User.objects.create_user(pk=42, username="organisation-delete", email="institute@example.org", is_active=True)
        self.organisation = OrganisationAccount.objects.create(
            user=self.user, name="Private Institute Name", email=self.user.email, website="https://example.org",
            description="Mathematical research.", representative_name="Private Representative", authority="Director.", status="approved",
        )
        EmailAddress.objects.create(user=self.user, email=self.user.email, primary=True, verified=True)

    def session(self, data):
        session = SessionStore()
        session.update(data)
        session.save()
        return session.session_key

    def owned_submission(self, *, owner=None):
        return Submission.objects.create(owner=owner or self.user, title="Retained research", repository_url="https://github.com/example/research")

    def proof(self, *, user=None, attempt_user=None):
        submission = self.owned_submission(owner=self.other)
        attempt = VerificationAttempt.objects.create(
            submission=submission, user=attempt_user or self.user, method="file", repository_url=submission.repository_url,
            repository_id=10, repository_owner_id=20, full_name="example/research", token_hash="a" * 64,
            expires_at=timezone.now() + timedelta(hours=1), consumed_at=timezone.now(),
        )
        return RepositoryVerification.objects.create(
            submission=submission, attempt=attempt, user=user or self.user, method="file", repository_url=submission.repository_url,
            repository_id=10, repository_owner_id=20, full_name="example/research", valid_until=timezone.now() - timedelta(days=1),
            revoked_at=timezone.now(),
        )

    def assert_retained(self):
        self.assertTrue(OrganisationAccount.objects.filter(pk=self.organisation.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_requires_active_staff_and_both_permissions(self):
        self.assertFalse(can_delete_organisation(None))
        self.assertFalse(can_delete_organisation(AnonymousUser()))
        self.assertTrue(can_delete_organisation(self.moderator))
        for index, (staff, active, permission_names) in enumerate((
            (False, True, ("change_organisationaccount", "delete_organisationaccount")),
            (True, True, ("change_organisationaccount",)),
            (True, True, ("delete_organisationaccount",)),
            (True, False, ("change_organisationaccount", "delete_organisationaccount")),
        )):
            actor = get_user_model().objects.create_user(username=f"insufficient-{index}", is_staff=staff, is_active=active)
            actor.user_permissions.add(*[permission for permission in self.permissions if permission.codename in permission_names])
            with self.subTest(actor=index):
                self.assertFalse(can_delete_organisation(actor))
                with self.assertRaises(ValidationError):
                    delete_organisation(self.organisation, moderator=actor)
                self.assert_retained()

    def test_service_rechecks_cached_permissions(self):
        self.assertTrue(can_delete_organisation(self.moderator))
        self.moderator.user_permissions.remove(Permission.objects.get(codename="delete_organisationaccount"))
        with self.assertRaises(ValidationError):
            delete_organisation(self.organisation, moderator=self.moderator)
        self.assert_retained()

    def test_pending_approved_and_rejected_accounts_can_be_deleted(self):
        for index, status in enumerate(("pending", "approved", "rejected")):
            user = get_user_model().objects.create_user(username=f"delete-{status}", email=f"{status}@example.org", is_active=status == "approved")
            organisation = OrganisationAccount.objects.create(user=user, name=status, email=user.email, website="https://example.org", status=status)
            with self.subTest(status=status):
                result = delete_organisation(organisation, moderator=self.moderator)
                self.assertEqual(result["user_id"], user.pk)
                self.assertFalse(OrganisationAccount.objects.filter(pk=organisation.pk).exists())
                self.assertFalse(get_user_model().objects.filter(pk=user.pk).exists())
        self.assert_retained()

    def test_deletes_login_auth_records_and_only_its_active_encrypted_sessions(self):
        SocialAccount.objects.create(user=self.user, provider="github", uid="organisation-legacy-link")
        TOTP.activate(self.user, generate_totp_secret())
        active = self.session({"_auth_user_id": str(self.user.pk), "private": "private-session-value"})
        pending = self.session({"account_login": Login(user=self.user, email=self.user.email).serialize()})
        request = RequestFactory().get("/")
        request.session = SessionStore()
        record_authentication(request, self.user, "code", email=self.user.email)
        request.session.save()
        factor = request.session.session_key
        unrelated = self.session({"_auth_user_id": str(self.other.pk)})
        unrelated_pending = self.session({"account_login": Login(user=self.other).serialize()})
        anonymous = self.session({"visitor": "anonymous"})
        expired = self.session({"_auth_user_id": str(self.user.pk)})
        Session.objects.filter(pk=expired).update(expire_date=timezone.now() - timedelta(days=1))
        result = delete_organisation(self.organisation, moderator=self.moderator)
        self.assertEqual(result["sessions_deleted"], 3)
        self.assertFalse(OrganisationAccount.objects.filter(pk=self.organisation.pk).exists())
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertFalse(EmailAddress.objects.filter(user_id=self.user.pk).exists())
        self.assertFalse(SocialAccount.objects.filter(user_id=self.user.pk).exists())
        self.assertFalse(Authenticator.objects.filter(user_id=self.user.pk).exists())
        self.assertFalse(Session.objects.filter(pk__in=(active, pending, factor)).exists())
        self.assertEqual(Session.objects.filter(pk__in=(unrelated, unrelated_pending, anonymous, expired)).count(), 4)
        self.assertTrue(get_user_model().objects.filter(pk=self.other.pk).exists())
        self.assertEqual(SessionStore(unrelated).load()["_auth_user_id"], str(self.other.pk))

    def test_owned_projects_block_deletion_and_stale_confirmation_is_rechecked(self):
        self.assertEqual(organisation_deletion_blockers(self.organisation), [])
        project = self.owned_submission()
        with self.assertRaisesMessage(ValidationError, "owns project submissions"):
            delete_organisation(self.organisation, moderator=self.moderator)
        self.assert_retained()
        self.assertTrue(Submission.objects.filter(pk=project.pk).exists())

    def test_expired_revoked_verification_evidence_blocks_deletion(self):
        proof = self.proof()
        with self.assertRaisesMessage(ValidationError, "repository-verification evidence"):
            delete_organisation(self.organisation, moderator=self.moderator)
        self.assert_retained()
        self.assertTrue(RepositoryVerification.objects.filter(pk=proof.pk).exists())

    def test_verification_protecting_an_attempt_also_blocks_deletion(self):
        proof = self.proof(user=self.other, attempt_user=self.user)
        with self.assertRaisesMessage(ValidationError, "repository-verification evidence"):
            delete_organisation(self.organisation, moderator=self.moderator)
        self.assert_retained()
        self.assertTrue(RepositoryVerification.objects.filter(pk=proof.pk).exists())

    def test_transferred_revision_and_unprotected_attempt_keep_project_history(self):
        project = self.owned_submission(owner=self.other)
        revision = SubmissionRevision.objects.create(submission=project, number=1, snapshot=project.snapshot(), submitted_by=self.user)
        snapshot = revision.snapshot.copy()
        attempt = VerificationAttempt.objects.create(
            submission=project, user=self.user, method="file", repository_url=project.repository_url,
            repository_id=10, repository_owner_id=20, full_name="example/research", token_hash="a" * 64,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        delete_organisation(self.organisation, moderator=self.moderator)
        revision.refresh_from_db()
        self.assertIsNone(revision.submitted_by)
        self.assertEqual(revision.snapshot, snapshot)
        self.assertTrue(Submission.objects.filter(pk=project.pk, owner=self.other).exists())
        self.assertFalse(VerificationAttempt.objects.filter(pk=attempt.pk).exists())

    def test_staff_superuser_and_self_targets_are_protected(self):
        for staff, superuser in ((True, False), (False, True)):
            get_user_model().objects.filter(pk=self.user.pk).update(is_staff=staff, is_superuser=superuser)
            with self.subTest(staff=staff, superuser=superuser):
                with self.assertRaisesMessage(ValidationError, "staff privileges"):
                    delete_organisation(self.organisation, moderator=self.moderator)
                self.assert_retained()
        get_user_model().objects.filter(pk=self.user.pk).update(is_staff=True, is_superuser=True)
        with self.assertRaisesMessage(ValidationError, "currently using"):
            delete_organisation(self.organisation, moderator=self.user)
        self.assert_retained()

    def test_former_administration_and_moderation_history_is_protected(self):
        project = self.owned_submission(owner=self.other)
        revision = SubmissionRevision.objects.create(submission=project, number=1, snapshot=project.snapshot(), reviewed_by=self.user)
        with self.assertRaisesMessage(ValidationError, "administration or moderation history"):
            delete_organisation(self.organisation, moderator=self.moderator)
        revision.delete()
        LogEntry.objects.log_actions(user_id=self.user.pk, queryset=[self.other], action_flag=ADDITION)
        with self.assertRaisesMessage(ValidationError, "administration or moderation history"):
            delete_organisation(self.organisation, moderator=self.moderator)
        self.assert_retained()

    def test_assigned_groups_and_permissions_require_separate_resolution(self):
        self.user.groups.add(Group.objects.create(name="Retained organisation role"))
        with self.assertRaisesMessage(ValidationError, "assigned roles or permissions"):
            delete_organisation(self.organisation, moderator=self.moderator)
        self.assert_retained()
        self.user.groups.clear()
        self.user.user_permissions.add(self.permissions[0])
        with self.assertRaisesMessage(ValidationError, "assigned roles or permissions"):
            delete_organisation(self.organisation, moderator=self.moderator)
        self.assert_retained()

    def test_protected_delete_rolls_back_org_user_sessions_and_log_redaction(self):
        active = self.session({"_auth_user_id": str(self.user.pk)})
        entry = LogEntry.objects.create(
            user=self.moderator, content_type=ContentType.objects.get_for_model(OrganisationAccount),
            object_id=str(self.organisation.pk), object_repr=self.organisation.name, action_flag=ADDITION,
            change_message="Private moderation details.",
        )
        with patch.object(get_user_model(), "delete", side_effect=ProtectedError("protected", {self.other})):
            with self.assertRaisesMessage(ValidationError, "nothing has been deleted"):
                delete_organisation(self.organisation, moderator=self.moderator)
        self.assert_retained()
        self.assertTrue(Session.objects.filter(pk=active).exists())
        entry.refresh_from_db()
        self.assertEqual(entry.object_repr, self.organisation.name)
        self.assertEqual(entry.change_message, "Private moderation details.")

    def test_logs_targeting_deleted_records_are_redacted_without_losing_deletion_audit(self):
        organisation_type = ContentType.objects.get_for_model(OrganisationAccount)
        entry = LogEntry.objects.create(user=self.moderator, content_type=organisation_type, object_id=str(self.organisation.pk), object_repr=self.organisation.name, action_flag=ADDITION, change_message=self.organisation.email)
        user_entry = LogEntry.objects.create(user=self.moderator, content_type=ContentType.objects.get_for_model(self.user), object_id=str(self.user.pk), object_repr=self.user.email, action_flag=ADDITION, change_message=self.user.email)
        label = f"Organisation account #{self.organisation.pk}"
        deletion = LogEntry.objects.create(user=self.moderator, content_type=organisation_type, object_id=str(self.organisation.pk), object_repr=label, action_flag=DELETION, change_message="")
        delete_organisation(self.organisation, moderator=self.moderator)
        for row in (entry, user_entry, deletion):
            row.refresh_from_db()
            self.assertNotIn("Private", row.object_repr)
            self.assertNotIn("@", row.object_repr)
            self.assertEqual(row.change_message, "")
        self.assertEqual(deletion.object_repr, label)
        self.assertEqual(deletion.action_flag, DELETION)
        self.assertEqual(deletion.user_id, self.moderator.pk)

    def test_queued_notice_disappears_and_worker_does_not_send_after_deletion(self):
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(notification_status="queued", reviewed_at=timezone.now())
        delete_organisation(self.organisation, moderator=self.moderator)
        self.assertEqual(process_organisation_notifications(), {"sent": 0, "failed": 0, "remaining": 0})
        self.assertEqual(len(mail.outbox), 0)

    def test_claimed_notice_blocks_stale_delete_until_worker_finishes(self):
        self.assertEqual(organisation_deletion_blockers(self.organisation), [])
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(notification_status="sending", notification_attempted_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "email is being sent"):
            delete_organisation(self.organisation, moderator=self.moderator)
        self.assert_retained()
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(notification_status="sent")
        delete_organisation(self.organisation, moderator=self.moderator)
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_worker_skips_a_queued_id_deleted_before_its_conditional_claim(self):
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(notification_status="queued", reviewed_at=timezone.now())
        ticks = iter((0, 1))

        def before_claim():
            tick = next(ticks)
            if tick == 1:
                delete_organisation(self.organisation, moderator=self.moderator)
            return tick

        with patch("accounts.organisation_notifications.monotonic", side_effect=before_claim):
            self.assertEqual(process_organisation_notifications(), {"sent": 0, "failed": 0, "remaining": 0})
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_delete_is_refused_while_worker_has_claimed_the_notice(self):
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(notification_status="queued", reviewed_at=timezone.now())

        def while_sending():
            with self.assertRaisesMessage(ValidationError, "email is being sent"):
                delete_organisation(self.organisation, moderator=self.moderator)
            self.assert_retained()
            return 1

        with patch("accounts.organisation_notifications.EmailMessage.send", side_effect=while_sending):
            self.assertEqual(process_organisation_notifications()["sent"], 1)
        delete_organisation(self.organisation, moderator=self.moderator)
        self.assertFalse(get_user_model().objects.filter(pk=self.user.pk).exists())

    def test_missing_organisation_is_a_safe_stale_confirmation_error(self):
        delete_organisation(self.organisation, moderator=self.moderator)
        with self.assertRaisesMessage(ValidationError, "already been deleted"):
            delete_organisation(self.organisation, moderator=self.moderator)
