"""Change-request emails use frozen contributor contacts and committed decisions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from allauth.account.internal.flows.login import record_authentication
from allauth.account.models import EmailAddress
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import close_old_connections, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .models import Submission, SubmissionRevision
from .notifications import can_send, notify_changes_requested, notify_contributors, notify_moderators


MAIL_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DEFAULT_FROM_EMAIL": "math-stoa <noreply@example.invalid>",
    "PUBLIC_BASE_URL": "https://math-stoa.example",
    "PUBLIC_CONTACT_EMAIL": "contact@example.invalid",
}


class ChangeNoticeFixtures:
    def setUp(self):
        super().setUp()
        users = get_user_model().objects
        self.owner = users.create_user(username="private-owner", email="untrusted@example.invalid")
        self.other = users.create_user(username="private-submitter")
        self.reviewer = users.create_superuser(username="private-moderator", email="moderator@example.invalid")
        self.contact = EmailAddress.objects.create(user=self.owner, email="verified@example.invalid", verified=True, primary=True)
        EmailAddress.objects.create(user=self.other, email="other@example.invalid", verified=True, primary=True)
        self.submission = Submission.objects.create(
            owner=self.owner, title="Frozen  title\r\nBcc: no-one", repository_url="https://github.com/example/research",
            collaborators=[{"name": "A, Contributor", "email": "contributor@example.invalid"}],
        )
        self.revision = SubmissionRevision.objects.create(
            submission=self.submission, number=1, snapshot=self.submission.snapshot(), submitted_by=self.other,
        )
        mail.outbox = []

    def reject(self):
        self.revision.review(self.reviewer, approve=False, note="Private feedback <script>alert(1)</script> contributor@example.invalid")


@override_settings(**MAIL_SETTINGS)
class ChangeNoticeTests(ChangeNoticeFixtures, TestCase):
    def test_rejection_sends_only_after_commit_callback(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.reject()
            self.assertEqual(mail.outbox, [])
            self.assertEqual(self.revision.status, "rejected")
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_content_uses_frozen_title_and_explains_owner_only_access_without_feedback(self):
        self.submission.title = "Newer draft title"
        self.submission.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.reject()
        message = mail.outbox[0]
        self.assertEqual(message.to, ["contributor@example.invalid"])
        self.assertEqual(message.cc + message.bcc, [])
        self.assertEqual(message.reply_to, ["contact@example.invalid"])
        self.assertEqual(message.from_email, MAIL_SETTINGS["DEFAULT_FROM_EMAIL"])
        self.assertEqual(message.subject, f"{settings.ACCOUNT_EMAIL_SUBJECT_PREFIX}Changes requested: Frozen title Bcc: no-one")
        self.assertNotIn("\n", message.subject)
        self.assertIn('revision 1 of "Frozen title Bcc: no-one"', message.body)
        self.assertIn(MAIL_SETTINGS["PUBLIC_BASE_URL"] + reverse("submissions:list"), message.body)
        self.assertIn("Only the project's owner can read the moderation feedback", message.body)
        self.assertIn("Being listed as a contributor does not grant access", message.body)
        self.assertIn("coordinate any changes with the person who submitted the project", message.body)
        self.assertIn("edit and save a new draft", message.body)
        self.assertIn("/privacy/", message.body)
        for secret in (self.owner.email, self.contact.email, "other@example.invalid", "contributor@example.invalid", self.reviewer.email,
                       self.owner.username, self.other.username, self.reviewer.username, str(self.submission.pk), self.submission.pk.hex,
                       "Newer draft title", "Private feedback", "<script>", "/admin/", "Στοά"):
            self.assertNotIn(secret, message.body)

    def test_each_recipient_matches_existing_submission_confirmations(self):
        self.revision.snapshot["collaborators"] += [
            {"name": "B, Contributor", "email": "second@example.invalid"},
            {"name": "C, Contributor", "email": ""},
            {"name": "D, Contributor"},
        ]
        SubmissionRevision.objects.filter(pk=self.revision.pk).update(snapshot=self.revision.snapshot)
        self.assertEqual(notify_contributors(self.revision), 2)
        confirmation_recipients = [message.to for message in mail.outbox]
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            self.reject()
        self.assertEqual([message.to for message in mail.outbox], confirmation_recipients)
        self.assertEqual(confirmation_recipients, [["contributor@example.invalid"], ["second@example.invalid"]])
        for message in mail.outbox:
            self.assertEqual(message.cc + message.bcc, [])
            for address in ("contributor@example.invalid", "second@example.invalid", self.contact.email, self.owner.email):
                self.assertNotIn(address, message.body)

    def test_frozen_contacts_are_used_despite_newer_draft_or_revision_before_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.reject()
            self.submission.collaborators = [{"name": "New, Contributor", "email": "newer@example.invalid"}]
            self.submission.revision = 2
            self.submission.save()
            SubmissionRevision.objects.create(submission=self.submission, number=2, snapshot=self.submission.snapshot())
        self.assertEqual(mail.outbox[0].to, ["contributor@example.invalid"])

    def test_missing_contributor_contacts_never_fall_back_to_account_emails(self):
        for contributors in ([], [{"name": "No, Email"}], [{"name": "Blank, Email", "email": ""}]):
            with self.subTest(contributors=contributors):
                self.revision.snapshot["collaborators"] = contributors
                SubmissionRevision.objects.filter(pk=self.revision.pk).update(snapshot=self.revision.snapshot, status="pending")
                with self.assertLogs("submissions.notifications", level="WARNING") as logs:
                    with self.captureOnCommitCallbacks(execute=True):
                        self.reject()
                self.assertIn("no contributor emails", logs.output[0])
                self.assertEqual(mail.outbox, [])
                self.assertEqual(self.revision.status, "rejected")

    def test_provider_only_account_needs_no_account_email_verification(self):
        EmailAddress.objects.all().delete()
        self.owner.email = ""
        self.owner.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.reject()
        self.assertEqual(mail.outbox[0].to, ["contributor@example.invalid"])
        self.assertEqual(self.revision.status, "rejected")

    @override_settings(EMAIL_LOGIN_ENABLED=False)
    def test_notifications_do_not_require_email_signin_enabled(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.reject()
        self.assertEqual(mail.outbox[0].to, ["contributor@example.invalid"])

    def test_partial_delivery_failure_does_not_stop_remaining_contributors(self):
        self.revision.snapshot["collaborators"] += [
            {"name": "B, Contributor", "email": "second@example.invalid"},
            {"name": "C, Contributor", "email": "third@example.invalid"},
        ]
        SubmissionRevision.objects.filter(pk=self.revision.pk).update(snapshot=self.revision.snapshot, status="rejected")
        with patch("submissions.notifications.EmailMessage.send", side_effect=[OSError("private@example.invalid credential"), 0, 1]) as send:
            with self.assertLogs("submissions.notifications", level="WARNING") as logs:
                self.assertEqual(notify_changes_requested(self.revision.pk), 1)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(len(logs.output), 2)
        self.assertNotIn("private@example.invalid", " ".join(logs.output))
        self.assertNotIn("credential", " ".join(logs.output))

    def test_legacy_header_injection_address_cannot_add_recipients_or_stop_other_deliveries(self):
        self.revision.snapshot["collaborators"].insert(0, {"name": "Bad, Contact", "email": "bad@example.invalid\r\nBcc: victim@example.invalid"})
        SubmissionRevision.objects.filter(pk=self.revision.pk).update(snapshot=self.revision.snapshot, status="rejected")
        with self.assertLogs("submissions.notifications", level="WARNING") as logs:
            self.assertEqual(notify_changes_requested(self.revision.pk), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["contributor@example.invalid"])
        self.assertEqual(mail.outbox[0].cc + mail.outbox[0].bcc, [])
        self.assertNotIn("victim@example.invalid", " ".join(logs.output))

    @override_settings(PUBLIC_CONTACT_EMAIL="")
    def test_missing_public_contact_does_not_prevent_notice_or_add_reply_recipients(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.reject()
        self.assertEqual(mail.outbox[0].reply_to, [])
        self.assertEqual(mail.outbox[0].to, ["contributor@example.invalid"])

    def test_failed_or_zero_delivery_keeps_decision_and_logs_no_address_or_exception(self):
        for failure in (OSError("credentials and verified@example.invalid"), None):
            with self.subTest(failure=failure):
                # Call the delivery function against an already committed-looking
                # decision to exercise both backend outcomes independently.
                SubmissionRevision.objects.filter(pk=self.revision.pk).update(status="rejected")
                with patch("submissions.notifications.EmailMessage.send", side_effect=failure, return_value=0):
                    with self.assertLogs("submissions.notifications", level="WARNING") as logs:
                        self.assertFalse(notify_changes_requested(self.revision.pk))
                self.assertNotIn(self.contact.email, " ".join(logs.output))
                self.assertNotIn("credentials", " ".join(logs.output))
                self.revision.refresh_from_db()
                self.assertEqual(self.revision.status, "rejected")

    def test_delivery_exception_never_unwinds_a_review(self):
        with patch("submissions.notifications.EmailMessage.send", side_effect=OSError("smtp down")):
            with self.assertLogs("submissions.notifications", level="WARNING"):
                with self.captureOnCommitCallbacks(execute=True):
                    self.reject()
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "rejected")
        self.assertEqual(mail.outbox, [])

    def test_missing_sender_or_smtp_host_skips_delivery_without_network(self):
        for config in ({"DEFAULT_FROM_EMAIL": ""}, {"EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend", "EMAIL_HOST": ""}):
            with self.subTest(config=config), override_settings(**config):
                SubmissionRevision.objects.filter(pk=self.revision.pk).update(status="rejected")
                with patch("submissions.notifications.EmailMessage.send") as send:
                    with self.assertLogs("submissions.notifications", level="WARNING"):
                        self.assertFalse(notify_changes_requested(self.revision.pk))
                send.assert_not_called()

    def test_duplicate_stale_review_sends_once(self):
        stale = SubmissionRevision.objects.get(pk=self.revision.pk)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.reject()
            with self.assertRaisesMessage(ValidationError, "already been moderated"):
                stale.review(self.reviewer, approve=False)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_failed_delivery_is_not_retried_by_repeating_the_action(self):
        with patch("submissions.notifications.EmailMessage.send", side_effect=OSError("smtp down")) as send:
            with self.assertLogs("submissions.notifications", level="WARNING"):
                with self.captureOnCommitCallbacks(execute=True):
                    self.reject()
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                with self.assertRaises(ValidationError):
                    self.reject()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(callbacks, [])

    def test_approval_does_not_schedule_change_request_email(self):
        self.revision.snapshot.update(abstract="Enough text for validation", category="math.PR")
        SubmissionRevision.objects.filter(pk=self.revision.pk).update(snapshot=self.revision.snapshot)
        with patch("repository_verification.services.require_verified"), patch("submissions.forms.review_form") as form, patch("scripts.validate.validate_archive"):
            form.return_value.is_valid.return_value = True
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self.revision.review(self.reviewer, approve=True)
        self.assertEqual(callbacks, [])
        self.assertEqual(mail.outbox, [])

    def test_pending_approved_and_deleted_revisions_do_not_send(self):
        self.assertFalse(notify_changes_requested(self.revision.pk))
        SubmissionRevision.objects.filter(pk=self.revision.pk).update(status="approved")
        self.assertFalse(notify_changes_requested(self.revision.pk))
        pk = self.revision.pk
        self.revision.delete()
        self.assertFalse(notify_changes_requested(pk))
        self.assertEqual(mail.outbox, [])

    def test_unauthorized_reviewers_cannot_change_state_or_schedule_email(self):
        users = get_user_model().objects
        unauthorized = [self.owner, users.create_user(username="staff-no-permission", is_staff=True),
                        users.create_user(username="inactive", is_staff=True, is_superuser=True, is_active=False)]
        view_only = users.create_user(username="view-only", is_staff=True)
        view_only.user_permissions.add(Permission.objects.get(codename="view_submissionrevision"))
        unauthorized.append(view_only)
        for actor in unauthorized:
            with self.subTest(actor=actor.username), self.captureOnCommitCallbacks(execute=True) as callbacks:
                with self.assertRaises(ValidationError):
                    self.revision.review(actor, approve=False)
                self.assertEqual(callbacks, [])
                self.revision.refresh_from_db()
                self.assertEqual(self.revision.status, "pending")
        self.assertEqual(mail.outbox, [])

    def test_staff_with_change_permission_can_notify(self):
        staff = get_user_model().objects.create_user(username="moderator", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="change_submissionrevision"))
        with self.captureOnCommitCallbacks(execute=True):
            self.revision.review(staff, approve=False)
        self.assertEqual(mail.outbox[0].to, ["contributor@example.invalid"])

    def test_contributor_notification_grants_no_private_submission_access(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.reject()
        contributor = get_user_model().objects.create_user(username="listed-contributor", email="contributor@example.invalid")
        EmailAddress.objects.create(user=contributor, email=contributor.email, verified=True, primary=True)
        self.client.force_login(contributor)
        self.assertEqual(self.client.get(reverse("submissions:detail", args=[self.submission.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("submissions:edit", args=[self.submission.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("submissions:list")), "Frozen")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("submissions:detail", args=[self.submission.pk]))
        self.assertContains(response, "Changes were requested for this version.")
        self.assertContains(response, "Private feedback &lt;script&gt;alert(1)&lt;/script&gt;")

    def test_owner_is_not_prompted_to_verify_an_account_email(self):
        EmailAddress.objects.all().delete()
        self.client.force_login(self.owner)
        response = self.client.get(reverse("submissions:detail", args=[self.submission.pk]))
        self.assertNotContains(response, "Your account has no verified email address")
        self.assertNotContains(response, "Add and verify an account email")

    def admin_login(self):
        self.client.force_login(self.reviewer)
        factor = TOTP.activate(self.reviewer, generate_totp_secret()).instance
        request = RequestFactory().get("/")
        request.session = self.client.session
        record_authentication(request, self.reviewer, "mfa", id=factor.pk, type="totp")
        request.session.save()

    def admin_reject(self):
        return self.client.post(reverse("admin:submissions_submissionrevision_changelist"), {
            "action": "request_changes", "_selected_action": [self.revision.pk],
        })

    def test_real_admin_bulk_action_notifies_and_duplicate_does_not(self):
        self.admin_login()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.admin_reject()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.admin_reject()
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(any("already been moderated" in str(message) for message in get_messages(response.wsgi_request)))

    def test_bulk_action_keeps_each_revision_and_recipient_separate(self):
        second = Submission.objects.create(owner=self.other, title="Second project", collaborators=[{"name": "Second, Person", "email": "second@example.invalid"}])
        other_revision = SubmissionRevision.objects.create(submission=second, number=1, snapshot=second.snapshot())
        self.admin_login()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("admin:submissions_submissionrevision_changelist"), {
                "action": "request_changes", "_selected_action": [self.revision.pk, other_revision.pk],
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(sorted(message.to[0] for message in mail.outbox), ["contributor@example.invalid", "second@example.invalid"])
        for message in mail.outbox:
            if message.to == ["contributor@example.invalid"]:
                self.assertIn("Frozen title Bcc: no-one", message.body)
                self.assertNotIn("Second project", message.body)
            else:
                self.assertIn("Second project", message.body)
                self.assertNotIn("Frozen", message.body)
            self.assertEqual(len(message.to), 1)

    def test_admin_states_missing_contact_and_mail_configuration_without_addresses(self):
        self.admin_login()
        for missing in ("contact", "configuration"):
            with self.subTest(missing=missing):
                snapshot = {**self.revision.snapshot, "collaborators": [] if missing == "contact" else [{"name": "A, Contributor", "email": "contributor@example.invalid"}]}
                SubmissionRevision.objects.filter(pk=self.revision.pk).update(status="pending", snapshot=snapshot)
                with override_settings(DEFAULT_FROM_EMAIL="" if missing == "configuration" else MAIL_SETTINGS["DEFAULT_FROM_EMAIL"]):
                    with self.assertLogs("submissions.notifications", level="WARNING"):
                        with self.captureOnCommitCallbacks(execute=True):
                            response = self.admin_reject()
                messages = " ".join(str(message) for message in get_messages(response.wsgi_request))
                self.assertIn("No email notification", messages)
                self.assertIn("no contributor email addresses" if missing == "contact" else "email delivery is not configured", messages)
                self.assertNotIn(self.contact.email, messages)
                self.assertNotIn("contributor@example.invalid", messages)
        self.assertEqual(mail.outbox, [])

    def test_admin_without_mfa_cannot_trigger_email(self):
        self.client.force_login(self.reviewer)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.admin_reject()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(callbacks, [])
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "pending")


@override_settings(**{
    **MAIL_SETTINGS,
    "DEBUG": False,
    "EMAIL_LOGIN_ENABLED": False,
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "EMAIL_HOST": "smtp.example.invalid",
    "MODERATION_NOTIFY_EMAIL": "moderators@example.invalid",
})
class NotificationTransportTests(ChangeNoticeFixtures, TestCase):
    """Every notification path shares the same production transport guard."""

    def setUp(self):
        super().setUp()
        SubmissionRevision.objects.filter(pk=self.revision.pk).update(status="rejected")

    def test_production_unencrypted_or_conflicting_smtp_never_attempts_any_send(self):
        for use_tls, use_ssl in ((False, False), (True, True)):
            with self.subTest(use_tls=use_tls, use_ssl=use_ssl):
                with override_settings(EMAIL_USE_TLS=use_tls, EMAIL_USE_SSL=use_ssl):
                    self.assertFalse(can_send())
                    with patch("submissions.notifications.send_mail") as send_mail, patch("submissions.notifications.EmailMessage.send") as send:
                        self.assertFalse(notify_moderators(self.revision))
                        self.assertEqual(notify_contributors(self.revision), 0)
                        with self.assertLogs("submissions.notifications", level="WARNING"):
                            self.assertFalse(notify_changes_requested(self.revision.pk))
                    send_mail.assert_not_called()
                    send.assert_not_called()

    def test_production_tls_and_ssl_preserve_all_existing_notification_paths(self):
        for use_tls, use_ssl in ((True, False), (False, True)):
            with self.subTest(use_tls=use_tls, use_ssl=use_ssl):
                with override_settings(EMAIL_USE_TLS=use_tls, EMAIL_USE_SSL=use_ssl):
                    self.assertTrue(can_send())
                    # SMTP is mocked: this verifies eligibility and dispatch,
                    # without contacting a server or sending a real message.
                    with patch("submissions.notifications.send_mail", return_value=1) as send_mail, patch("submissions.notifications.EmailMessage.send", return_value=1) as send:
                        self.assertTrue(notify_moderators(self.revision))
                        self.assertEqual(notify_contributors(self.revision), 1)
                        self.assertTrue(notify_changes_requested(self.revision.pk))
                    self.assertEqual(send_mail.call_count, 1)
                    self.assertEqual(send.call_count, 2)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", EMAIL_USE_TLS=False, EMAIL_USE_SSL=False)
    def test_in_memory_backend_remains_usable_without_smtp_encryption(self):
        self.assertTrue(can_send())
        self.assertTrue(notify_moderators(self.revision))
        self.assertEqual(notify_contributors(self.revision), 1)
        self.assertTrue(notify_changes_requested(self.revision.pk))
        self.assertEqual(len(mail.outbox), 3)

    @override_settings(DEBUG=True, EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend", EMAIL_USE_TLS=False, EMAIL_USE_SSL=False)
    def test_development_console_backend_remains_available(self):
        self.assertTrue(can_send())


@override_settings(**MAIL_SETTINGS)
class ChangeNoticeTransactionTests(ChangeNoticeFixtures, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_review_attempts_schedule_only_one_notice(self):
        start = Barrier(2, timeout=5)

        def review():
            close_old_connections()
            try:
                revision = SubmissionRevision.objects.get(pk=self.revision.pk)
                moderator = get_user_model().objects.get(pk=self.reviewer.pk)
                start.wait()
                try:
                    revision.review(moderator, approve=False)
                except ValidationError as error:
                    return error.messages
                return ["decided"]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result(timeout=10) for future in [pool.submit(review), pool.submit(review)]]
        self.assertEqual(sorted(results), [["This revision has already been moderated."], ["decided"]])
        self.assertEqual(len(mail.outbox), 1)

    def test_outer_transaction_must_commit_before_delivery(self):
        with transaction.atomic():
            with transaction.atomic():
                self.reject()
            self.assertEqual(mail.outbox, [])
        self.assertEqual(len(mail.outbox), 1)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "rejected")

    def test_outer_rollback_discards_decision_and_email(self):
        with self.assertRaisesMessage(RuntimeError, "rollback"):
            with transaction.atomic():
                self.reject()
                raise RuntimeError("rollback")
        self.assertEqual(mail.outbox, [])
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "pending")

    def test_inner_savepoint_rollback_discards_email_even_if_outer_commits(self):
        with transaction.atomic():
            try:
                with transaction.atomic():
                    self.reject()
                    raise RuntimeError("rollback")
            except RuntimeError:
                pass
        self.assertEqual(mail.outbox, [])
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "pending")

    def test_failed_decision_save_sends_nothing(self):
        with patch.object(SubmissionRevision, "save", side_effect=RuntimeError("save failed")):
            with self.assertRaisesMessage(RuntimeError, "save failed"):
                self.reject()
        self.assertEqual(mail.outbox, [])
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "pending")

    def test_delivery_observes_committed_state_outside_atomic_block(self):
        def send(message):
            self.assertFalse(transaction.get_connection().in_atomic_block)
            self.assertEqual(SubmissionRevision.objects.get(pk=self.revision.pk).status, "rejected")
            return 1
        with patch("submissions.notifications.EmailMessage.send", autospec=True, side_effect=send) as delivery:
            self.reject()
        delivery.assert_called_once()

    def test_unexpected_callback_failure_keeps_committed_decision_and_other_callbacks(self):
        later = []
        with patch("submissions.notifications.notify_changes_requested", side_effect=RuntimeError("unexpected")):
            with self.assertLogs("django.db.backends.base", level="ERROR"):
                with transaction.atomic():
                    self.reject()
                    transaction.on_commit(lambda: later.append(True))
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "rejected")
        self.assertEqual(later, [True])
