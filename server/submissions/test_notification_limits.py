"""Outbox abuse limits, idempotency, erasure, failures and concurrency."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from .models import NotificationDelivery, Submission, SubmissionRevision
from .notifications import notify_contributors, notify_changes_requested, process_notification_queue

MAIL_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DEFAULT_FROM_EMAIL": "site@example.invalid",
    "PUBLIC_BASE_URL": "https://math-stoa.example",
    "PUBLIC_CONTACT_EMAIL": "contact@example.invalid",
}


class Fixtures:
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user(username="notification-owner")
        self.number = 0
        mail.outbox = []

    def revision(self, *, people=None, owner=None, status="pending"):
        self.number += 1
        submission = Submission.objects.create(
            owner=owner or self.owner, title="A mathematical project",
            collaborators=people if people is not None else [{"name": "A, Contributor", "email": "person@example.invalid"}],
        )
        return SubmissionRevision.objects.create(submission=submission, number=1, snapshot=submission.snapshot(), status=status)


@override_settings(**MAIL_SETTINGS)
class NotificationLimitsTests(Fixtures, TestCase):
    def test_one_hundred_rows_with_same_address_enqueue_and_send_once(self):
        people = [{"name": f"Person{i}, Contributor", "orcid": str(i), "email": "SAME@EXAMPLE.INVALID" if i % 2 else "same@example.invalid"} for i in range(100)]
        revision = self.revision(people=people)
        with patch("submissions.notifications.EmailMessage.send", side_effect=AssertionError("SMTP in web request")):
            self.assertEqual(notify_contributors(revision), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        job = NotificationDelivery.objects.get()
        self.assertNotIn("@", repr(job.__dict__))
        self.assertEqual(len(job.recipient_digest), 64)
        self.assertEqual(process_notification_queue()["sent"], 1)
        self.assertEqual([m.to for m in mail.outbox], [["same@example.invalid"]])
        self.assertEqual(notify_contributors(revision), 0)
        self.assertEqual(process_notification_queue()["sent"], 0)

    def test_legacy_bad_addresses_are_not_enqueued(self):
        people = [None, {"email": 5}, {"email": "not an email"}, {"email": "safe@example.invalid\r\nBcc: other@example.invalid"}, {"name": "Person", "email": "good@example.invalid"}]
        self.assertEqual(notify_contributors(self.revision(people=people)), 1)
        process_notification_queue()
        self.assertEqual([m.to for m in mail.outbox], [["good@example.invalid"]])

    def test_recipient_hour_limit_across_owners_and_separate_notice_kind(self):
        first = self.revision()
        self.assertEqual(notify_contributors(first), 1)
        other = get_user_model().objects.create_user(username="other-notification-owner")
        self.assertEqual(notify_contributors(self.revision(owner=other)), 0)
        changes = self.revision(status="rejected")
        self.assertEqual(notify_changes_requested(changes.pk), 1)
        self.assertEqual(process_notification_queue()["sent"], 2)

    def test_combined_recipient_daily_limit(self):
        now = timezone.now()
        for index in range(6):
            with patch("submissions.notifications.timezone.now", return_value=now + timedelta(hours=2 * index)):
                revision = self.revision(status="rejected")
                enqueue = notify_contributors(revision) if index % 2 == 0 else notify_changes_requested(revision.pk)
                self.assertEqual(enqueue, int(index < 5))
                process_notification_queue()
        self.assertEqual(len(mail.outbox), 5)

    def test_account_and_global_limits_apply_to_distinct_recipients(self):
        other = get_user_model().objects.create_user(username="other-notification-owner")
        def rev(address, owner=None):
            return self.revision(people=[{"name": "Person", "email": address}], owner=owner)
        with patch("submissions.notifications.ACCOUNT_DAILY_LIMIT", 2), patch("submissions.notifications.GLOBAL_HOURLY_LIMIT", 3):
            self.assertEqual(notify_contributors(rev("one@example.invalid")), 1)
            self.assertEqual(notify_contributors(rev("two@example.invalid")), 1)
            self.assertEqual(notify_contributors(rev("three@example.invalid")), 0)
            self.assertEqual(notify_contributors(rev("four@example.invalid", other)), 1)
            self.assertEqual(notify_contributors(rev("five@example.invalid", other)), 0)

    def test_pending_and_retained_queue_caps(self):
        first = self.revision()
        second = self.revision(people=[{"name": "Other", "email": "other@example.invalid"}])
        with patch("submissions.notifications.MAX_PENDING", 1):
            self.assertEqual(notify_contributors(first), 1)
            self.assertEqual(notify_contributors(second), 0)
        process_notification_queue()
        with patch("submissions.notifications.MAX_RETAINED", 1):
            self.assertEqual(notify_contributors(self.revision(people=[{"name": "Third", "email": "third@example.invalid"}])), 0)
        self.assertEqual(NotificationDelivery.objects.count(), 1)

    def test_suppression_is_not_retried_later(self):
        first, second = self.revision(), self.revision()
        self.assertEqual(notify_contributors(first), 1)
        self.assertEqual(notify_contributors(second), 0)
        with patch("submissions.notifications.timezone.now", return_value=timezone.now() + timedelta(hours=2)):
            self.assertEqual(notify_contributors(second), 0)

    def test_actual_attempt_quotas_prevent_delayed_backlog_burst(self):
        now = timezone.now()
        first = self.revision()
        with patch("submissions.notifications.timezone.now", return_value=now - timedelta(hours=2)):
            self.assertEqual(notify_contributors(first), 1)
        self.assertEqual(notify_contributors(self.revision()), 1)
        result = process_notification_queue()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["suppressed"], 1)

    def test_removed_contributor_cancels_queued_message(self):
        revision = self.revision()
        notify_contributors(revision)
        SubmissionRevision.objects.filter(pk=revision.pk).update(snapshot={**revision.snapshot, "collaborators": []})
        self.assertEqual(process_notification_queue()["cancelled"], 1)
        self.assertEqual(mail.outbox, [])

    def test_erased_submission_cascades_queue_records(self):
        revision = self.revision()
        notify_contributors(revision)
        revision.submission.delete()
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertEqual(process_notification_queue()["sent"], 0)

    def test_failed_or_ambiguous_send_is_terminal_and_logs_no_address(self):
        notify_contributors(self.revision())
        with patch("submissions.notifications.EmailMessage.send", side_effect=RuntimeError("secret password person@example.invalid")), self.assertLogs("submissions.notifications", level="WARNING") as logs:
            self.assertEqual(process_notification_queue()["failed"], 1)
        self.assertNotIn("person@example.invalid", str(logs.output))
        self.assertNotIn("secret password", str(logs.output))
        with patch("submissions.notifications.EmailMessage.send") as send:
            process_notification_queue()
        send.assert_not_called()

    def test_crashed_claim_is_not_retried(self):
        notify_contributors(self.revision())
        NotificationDelivery.objects.update(status="sending", attempted_at=timezone.now() - timedelta(minutes=11))
        self.assertEqual(process_notification_queue()["failed"], 1)
        self.assertEqual(mail.outbox, [])

    def test_expired_jobs_and_old_records_are_cleaned_without_losing_idempotency(self):
        now = timezone.now()
        revision = self.revision()
        with patch("submissions.notifications.timezone.now", return_value=now - timedelta(days=2)):
            notify_contributors(revision)
        self.assertEqual(process_notification_queue()["expired"], 1)
        with patch("submissions.notifications.timezone.now", return_value=now + timedelta(days=8)):
            process_notification_queue()
            self.assertFalse(NotificationDelivery.objects.exists())
            self.assertEqual(notify_contributors(revision), 0)
        revision.refresh_from_db()
        self.assertEqual(len(revision.notification_keys), 1)
        self.assertNotIn("@", str(revision.notification_keys))


@override_settings(**MAIL_SETTINGS)
class NotificationTransactionTests(Fixtures, TransactionTestCase):
    def test_rollback_removes_jobs_and_tombstones(self):
        revision = self.revision()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.assertEqual(notify_contributors(revision), 1)
                raise RuntimeError("rollback")
        revision.refresh_from_db()
        self.assertEqual(revision.notification_keys, [])
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertEqual(process_notification_queue()["sent"], 0)

    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_workers_claim_once(self):
        notify_contributors(self.revision())
        barrier = Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return process_notification_queue(batch_size=1)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(sum(r["sent"] for r in results), 1)
        self.assertEqual(len(mail.outbox), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_enqueues_reserve_one_recipient_slot(self):
        revisions = [self.revision(), self.revision()]
        barrier = Barrier(2)
        def run(revision):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return notify_contributors(revision)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, revisions))
        self.assertEqual(sum(results), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 1)
