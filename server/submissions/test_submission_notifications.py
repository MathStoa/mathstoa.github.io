"""One-step submissions freeze the submitted version and notify its contributors."""
from datetime import timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import NotificationDelivery, Submission, SubmissionRevision
from .notifications import notify_submission_received, process_notification_queue
from .publication import DECLARATION, DECLARATION_VERSION, ABSTRACT_LICENSE


MAIL_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DEFAULT_FROM_EMAIL": "math-stoa <noreply@example.invalid>",
    "PUBLIC_BASE_URL": "https://math-stoa.example",
    "PUBLIC_CONTACT_EMAIL": "contact@example.invalid",
    "EMAIL_LOGIN_ENABLED": False,
}


@override_settings(**MAIL_SETTINGS)
class SubmissionNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username="provider-only-owner")
        SocialAccount.objects.create(user=self.owner, provider="github", uid="12345")
        self.other = get_user_model().objects.create_user(username="another-owner")
        self.client.force_login(self.owner)
        self.payload = {
            "title": "Probability research notes",
            "repository_url": "https://github.com/example/research",
            "abstract": "These research notes explore a mathematical question, explain the current partial results, and describe useful future work.",
            "category": "math.PR",
            "collaborators-name": ["Anna, Example", "Ben, Example"],
            "collaborators-orcid": ["", ""],
            "collaborators-email": ["anna@university.example", "ben@university.example"],
            "permission_confirmed": "on",
            "revision": "1",
        }
        mail.outbox = []

    def create(self, **changes):
        response = self.client.post(reverse("submissions:create"), {**self.payload, **changes})
        self.assertEqual(response.status_code, 302)
        return Submission.objects.latest("created_at")

    def edit(self, submission, **changes):
        response = self.client.post(
            reverse("submissions:edit", args=[submission.pk]),
            {**self.payload, "revision": str(submission.revision), **changes},
        )
        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        return response

    def test_create_queues_each_contributor_without_smtp_or_moderation(self):
        with patch("submissions.notifications.EmailMessage.send", side_effect=AssertionError("SMTP in web request")):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                submission = self.create()
        self.assertEqual(callbacks, [])
        self.assertEqual(mail.outbox, [])
        self.assertEqual(self.owner.email, "")
        self.assertFalse(EmailAddress.objects.filter(user=self.owner).exists())
        revision = submission.revisions.get()
        self.assertEqual(revision.status, "unverified")
        self.assertEqual(revision.submitted_by, self.owner)
        declaration = revision.snapshot["publication_declaration"]
        self.assertEqual(declaration["text"], DECLARATION)
        self.assertEqual(declaration["version"], DECLARATION_VERSION)
        self.assertEqual(declaration["license"], ABSTRACT_LICENSE)
        self.assertTrue(declaration["accepted_at"])
        self.assertIsNone(submission.published_revision_id)
        jobs = NotificationDelivery.objects.filter(revision__submission=submission)
        self.assertEqual(jobs.count(), 2)
        self.assertEqual(set(jobs.values_list("kind", "revision_id")), {("created", revision.pk)})
        self.assertEqual(process_notification_queue()["sent"], 2)
        self.assertEqual({tuple(message.to) for message in mail.outbox}, {
            ("anna@university.example",), ("ben@university.example",),
        })

    def test_edit_queues_updated_notice_and_preserves_published_revision(self):
        submission = self.create()
        published = submission.revisions.get()
        SubmissionRevision.objects.filter(pk=published.pk).update(status="approved", review_note="Private moderation feedback")
        Submission.objects.filter(pk=submission.pk).update(published_revision=published)
        with patch("submissions.notifications.EmailMessage.send", side_effect=AssertionError("SMTP in web request")):
            self.edit(submission, title="Updated research notes")
        self.assertEqual(submission.revision, 2)
        self.assertEqual(submission.published_revision_id, published.pk)
        self.assertEqual(submission.revisions.count(), 2)
        published.refresh_from_db()
        self.assertEqual(published.snapshot["title"], self.payload["title"])
        jobs = NotificationDelivery.objects.filter(revision__submission=submission, kind="updated")
        self.assertEqual(jobs.count(), 2)
        self.assertEqual(set(jobs.values_list("revision__number", "revision__snapshot__title")), {(2, "Updated research notes")})
        process_notification_queue()
        for message in mail.outbox:
            self.assertNotIn("Private moderation feedback", message.body)

    def test_create_edit_and_legacy_submit_retry_send_once_per_form_submission(self):
        submission = self.create()
        self.edit(submission)
        with patch("repository_verification.services.require_verified"):
            response = self.client.post(reverse("submissions:submit", args=[submission.pk]), {
                "revision": submission.revision, "publication_confirmed": "on",
            })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(submission.revisions.count(), 2)
        self.assertEqual(set(NotificationDelivery.objects.values_list("kind", flat=True)), {"created", "updated"})
        self.assertEqual(process_notification_queue()["sent"], 4)

    def test_receipt_uses_saved_title_and_does_not_share_addresses_or_access(self):
        submission = self.create()
        self.edit(submission, title="A newer draft title")
        process_notification_queue()
        created = [message for message in mail.outbox if self.payload["title"] in message.subject]
        updated = [message for message in mail.outbox if "A newer draft title" in message.subject]
        self.assertEqual(len(created), 2)
        self.assertEqual(len(updated), 2)
        for message in created:
            self.assertEqual(message.subject, f"[math-stoa]: Submitted — {self.payload['title']}")
            self.assertIn(f"A submission for “{self.payload['title']}” has been created on mathStoa.", message.body)
        for message in updated:
            self.assertEqual(message.subject, "[math-stoa]: Revision — A newer draft title")
            self.assertIn("saved as revision 2", message.body)
            self.assertIn("Changes must be moderated and approved before appearing publicly.", message.body)
            self.assertIn("Any previously published version remains unchanged in the meantime.", message.body)
        for messages in (created, updated):
            for message in messages:
                self.assertIn(MAIL_SETTINGS["PUBLIC_BASE_URL"] + reverse("submissions:list"), message.body)
                self.assertIn("You’re receiving this confirmation because you’re listed as a contributor.", message.body)
                self.assertIn("The owner can review the submission through My submissions:", message.body)
                self.assertIn("If you should not be listed, or your details are incorrect, please reply to this email.", message.body)
                self.assertTrue(message.body.endswith("The math-stoa team\n"))
                self.assertEqual(message.from_email, MAIL_SETTINGS["DEFAULT_FROM_EMAIL"])
                self.assertEqual(message.reply_to, [MAIL_SETTINGS["PUBLIC_CONTACT_EMAIL"]])
                self.assertEqual(message.cc + message.bcc, [])
                for address in self.payload["collaborators-email"]:
                    self.assertNotIn(address, message.body)
                self.assertNotIn(str(submission.pk), message.body)
                self.assertNotIn(self.owner.username, message.body)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("submissions:detail", args=[submission.pk])).status_code, 404)

    def test_blank_contacts_are_invalid_and_do_not_fall_back_to_account_addresses(self):
        self.owner.email = "untrusted@example.invalid"
        self.owner.save(update_fields=["email"])
        EmailAddress.objects.create(user=self.owner, email="verified@example.invalid", primary=True, verified=True)
        response = self.client.post(reverse("submissions:create"), {**self.payload, "collaborators-email": ["", ""]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Submission.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertEqual(process_notification_queue()["sent"], 0)
        self.assertEqual(mail.outbox, [])

    def test_addresses_are_deduplicated_without_copying_them_into_jobs(self):
        submission = self.create(**{"collaborators-email": ["SAME@EXAMPLE.INVALID", "same@example.invalid"]})
        job = NotificationDelivery.objects.get(revision__submission=submission)
        self.assertNotIn("@", repr(job.__dict__))
        self.assertEqual(len(job.recipient_digest), 64)
        self.assertEqual(process_notification_queue()["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["same@example.invalid"])

    def test_invalid_create_does_not_save_or_enqueue(self):
        response = self.client.post(reverse("submissions:create"), {**self.payload, "title": ""})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Submission.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())

    def test_each_form_submission_requires_fresh_declaration_acceptance(self):
        payload = {key: value for key, value in self.payload.items() if key != "permission_confirmed"}
        self.assertEqual(self.client.post(reverse("submissions:create"), payload).status_code, 200)
        self.assertFalse(Submission.objects.exists())
        submission = self.create()
        response = self.client.get(reverse("submissions:edit", args=[submission.pk]))
        self.assertFalse(response.context["form"]["permission_confirmed"].value())
        response = self.client.post(reverse("submissions:edit", args=[submission.pk]), payload)
        self.assertEqual(response.status_code, 200)
        submission.refresh_from_db()
        self.assertEqual(submission.revision, 1)
        self.assertEqual(submission.revisions.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 2)

    def test_unverified_submission_cannot_be_moderated(self):
        submission = self.create()
        reviewer = get_user_model().objects.create_superuser(username="moderator", email="staff@example.invalid")
        revision = submission.revisions.get()
        with patch("repository_verification.services.require_verified") as verification:
            for approve in (True, False):
                with self.subTest(approve=approve), self.assertRaises(ValidationError):
                    revision.review(reviewer, approve=approve, note="Review attempted too soon")
        verification.assert_not_called()
        revision.refresh_from_db()
        self.assertEqual(revision.status, "unverified")

    def test_existing_verification_is_rechecked_before_entering_moderation(self):
        with patch("repository_verification.services.has_current_verification", return_value=True):
            with patch("repository_verification.services.require_verified") as verification:
                submission = self.create()
        self.assertEqual(submission.revisions.get().status, "pending")
        verification.assert_called_once()

    def test_failed_live_recheck_requires_verification_and_preserves_publication(self):
        submission = self.create()
        published = submission.revisions.get()
        SubmissionRevision.objects.filter(pk=published.pk).update(status="approved")
        Submission.objects.filter(pk=submission.pk).update(published_revision=published)
        with patch("repository_verification.services.has_current_verification", return_value=True), patch(
            "repository_verification.views.has_current_verification", return_value=True,
        ), patch(
            "repository_verification.services.require_verified",
            side_effect=ValidationError("GitHub is unavailable. Please try again later."),
        ) as verification:
            response = self.client.post(reverse("submissions:edit", args=[submission.pk]), {
                **self.payload, "title": "Updated project awaiting verification",
            }, follow=True)
        verification.assert_called_once()
        self.assertRedirects(response, reverse("verification:overview", args=[submission.pk]))
        self.assertFalse(response.context["verified"])
        self.assertContains(response, "Repository access must be verified before moderation.")
        self.assertNotContains(response, "Repository authorization verified.")
        submission.refresh_from_db()
        published.refresh_from_db()
        current = submission.revisions.get(number=2)
        self.assertEqual(current.status, "unverified")
        self.assertEqual(current.snapshot["title"], "Updated project awaiting verification")
        self.assertEqual(submission.published_revision_id, published.pk)
        self.assertEqual(published.status, "approved")
        self.assertEqual(published.snapshot["title"], self.payload["title"])

    def test_new_submission_supersedes_earlier_unverified_version(self):
        submission = self.create()
        original = submission.revisions.get()
        self.edit(submission, title="Current submission")
        original.refresh_from_db()
        self.assertEqual(original.status, "superseded")
        current = submission.revisions.get(number=2)
        self.assertEqual(current.status, "unverified")
        self.assertEqual(original.snapshot["title"], self.payload["title"])
        self.assertEqual(current.snapshot["title"], "Current submission")

    def test_invalid_stale_and_nonowner_edits_do_not_enqueue(self):
        submission = self.create()
        self.edit(submission)
        count = NotificationDelivery.objects.count()
        url = reverse("submissions:edit", args=[submission.pk])
        invalid = self.client.post(url, {**self.payload, "revision": submission.revision, "title": ""})
        self.assertEqual(invalid.status_code, 200)
        stale = self.client.post(url, {**self.payload, "title": "Stale changes", "revision": "1"})
        self.assertEqual(stale.status_code, 409)
        self.client.force_login(self.other)
        unauthorized = self.client.post(url, {**self.payload, "revision": submission.revision})
        self.assertEqual(unauthorized.status_code, 404)
        self.assertEqual(NotificationDelivery.objects.count(), count)
        submission.refresh_from_db()
        self.assertEqual(submission.revision, 2)
        self.assertEqual(submission.title, self.payload["title"])

    def test_create_and_edit_queue_roll_back_with_saved_content(self):
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction.atomic():
                self.create()
                raise RuntimeError("rollback")
        self.assertFalse(Submission.objects.exists())
        self.assertFalse(NotificationDelivery.objects.exists())
        submission = self.create()
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction.atomic():
                self.edit(submission, title="Rolled back title")
                raise RuntimeError("rollback")
        submission.refresh_from_db()
        self.assertEqual(submission.revision, 1)
        self.assertEqual(submission.revisions.count(), 1)
        self.assertEqual(submission.title, self.payload["title"])
        self.assertFalse(NotificationDelivery.objects.filter(kind="updated").exists())

    def test_queue_failure_rolls_back_create_and_edit_in_the_view(self):
        with patch("submissions.views.notify_submission_received", side_effect=RuntimeError("queue unavailable")):
            with self.assertRaisesRegex(RuntimeError, "queue unavailable"):
                self.create()
        self.assertFalse(Submission.objects.exists())
        submission = self.create()
        with patch("submissions.views.notify_submission_received", side_effect=RuntimeError("queue unavailable")):
            with self.assertRaisesRegex(RuntimeError, "queue unavailable"):
                self.edit(submission, title="An unsaved change")
        submission.refresh_from_db()
        self.assertEqual(submission.revision, 1)
        self.assertEqual(submission.title, self.payload["title"])
        self.assertEqual(NotificationDelivery.objects.count(), 2)

    def test_repeated_edits_obey_recipient_limits_without_blocking_saves(self):
        submission = self.create()
        self.edit(submission)
        self.edit(submission, title="Third saved draft")
        self.assertEqual(submission.revision, 3)
        self.assertEqual(submission.revisions.count(), 3)
        self.assertEqual(NotificationDelivery.objects.filter(kind="updated").count(), 2)
        self.assertEqual(process_notification_queue()["sent"], 4)
        with patch("submissions.notifications.timezone.now", return_value=timezone.now() + timedelta(hours=2)):
            self.assertEqual(notify_submission_received(submission.revisions.get(number=submission.revision)), 0)

    def test_repeated_enqueue_stays_idempotent_after_record_cleanup(self):
        submission = self.create()
        self.assertEqual(notify_submission_received(submission.revisions.get(number=submission.revision)), 0)
        self.assertEqual(process_notification_queue()["sent"], 2)
        with patch("submissions.notifications.timezone.now", return_value=timezone.now() + timedelta(days=8)):
            process_notification_queue()
            self.assertFalse(NotificationDelivery.objects.exists())
            self.assertEqual(notify_submission_received(submission.revisions.get(number=submission.revision)), 0)
        self.assertEqual(len(mail.outbox), 2)

    def test_pending_queue_cap_also_bounds_submission_notices(self):
        with patch("submissions.notifications.MAX_PENDING", 1):
            submission = self.create()
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(notify_submission_received(submission.revisions.get(number=submission.revision)), 0)
        self.assertEqual(process_notification_queue()["sent"], 1)

    def test_later_contact_edit_does_not_retarget_frozen_confirmation(self):
        submission = self.create()
        Submission.objects.filter(pk=submission.pk).update(collaborators=[
            {"name": "Anna, Example", "email": "replacement@example.invalid"},
            {"name": "Ben, Example", "email": "ben@university.example"},
        ])
        self.assertEqual(process_notification_queue()["sent"], 2)
        self.assertEqual({tuple(message.to) for message in mail.outbox}, {
            ("anna@university.example",), ("ben@university.example",),
        })

    def test_erasing_contact_from_snapshot_cancels_notice_without_redirecting_it(self):
        submission = self.create()
        revision = submission.revisions.get()
        SubmissionRevision.objects.filter(pk=revision.pk).update(snapshot={
            **revision.snapshot,
            "collaborators": [{"name": "Ben, Example", "email": "ben@university.example"}],
        })
        result = process_notification_queue()
        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual([message.to for message in mail.outbox], [["ben@university.example"]])

    def test_erasing_submission_cascades_queued_notices(self):
        submission = self.create()
        submission.delete()
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertEqual(process_notification_queue()["sent"], 0)

    def test_delivery_failure_preserves_submission_and_is_not_retried(self):
        submission = self.create()
        with patch("submissions.notifications.EmailMessage.send", side_effect=OSError("SMTP is unavailable")):
            with self.assertLogs("submissions.notifications", level="WARNING"):
                self.assertEqual(process_notification_queue()["failed"], 2)
        submission.refresh_from_db()
        self.assertEqual(submission.title, self.payload["title"])
        self.assertEqual(submission.revision, 1)
        with patch("submissions.notifications.EmailMessage.send") as send:
            process_notification_queue()
        send.assert_not_called()
