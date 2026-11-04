"""The revision change form saves comments and decides through the real review path."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from allauth.account.internal.flows.login import record_authentication
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .admin import SubmissionRevisionAdmin
from .models import NotificationDelivery, Submission, SubmissionRevision
from .notifications import process_notification_queue


class IndividualReviewFixtures:
    def setUp(self):
        super().setUp()
        cache.clear()
        users = get_user_model().objects
        self.owner = users.create_user(username="individual-owner")
        self.reviewer = users.create_user(username="individual-reviewer", is_staff=True)
        self.reviewer.user_permissions.add(Permission.objects.get(codename="change_submissionrevision"))
        self.submission = Submission.objects.create(
            owner=self.owner, title="A mathematical research project",
            repository_url="https://github.com/example/research", category="math.NT",
            abstract="These research notes explain a mathematical question, the current partial results, and useful future work.",
            collaborators=[{"name": "Ada, Example", "email": "frozen@example.invalid"}],
            permission_confirmed=True, license_confirmed=True,
        )
        self.revision = SubmissionRevision.objects.create(
            submission=self.submission, number=1, snapshot=self.submission.snapshot(),
            submitted_by=self.owner, review_note="Earlier saved comments",
        )
        self.url = reverse("admin:submissions_submissionrevision_change", args=[self.revision.pk])
        self.queue = reverse("admin:submissions_submissionrevision_changelist")

    def login(self, user=None, client=None, mfa=True):
        user = user or self.reviewer
        client = client or self.client
        client.force_login(user)
        if mfa and user.is_staff:
            factor = Authenticator.objects.filter(user=user, type=Authenticator.Type.TOTP).first()
            if factor is None:
                factor = TOTP.activate(user, generate_totp_secret()).instance
            request = RequestFactory().get("/")
            request.session = client.session
            record_authentication(request, user, "mfa", id=factor.pk, type="totp")
            request.session.save()
        return client

    def assert_pending(self):
        self.revision.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual(self.revision.status, "pending")
        self.assertEqual(self.revision.review_note, "Earlier saved comments")
        self.assertIsNone(self.revision.reviewed_by)
        self.assertIsNone(self.revision.reviewed_at)
        self.assertIsNone(self.submission.published_revision_id)
        self.assertFalse(NotificationDelivery.objects.exists())

    def assert_bound_note(self, response, note):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["adminform"].form["review_note"].value(), note)
        self.assertTrue(response.context["adminform"].form.non_field_errors())


@override_settings(DEFAULT_FROM_EMAIL="notice@example.invalid", PUBLIC_BASE_URL="https://mathstoa.example")
class IndividualReviewTests(IndividualReviewFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_pending_form_offers_decisions_alongside_standard_save_controls(self):
        response = self.client.get(self.url)
        for name in ("_save", "_continue", "_approve", "_request_changes"):
            self.assertContains(response, f'name="{name}"', count=1)
        # An implicit form submit must keep the ordinary Save as its default.
        self.assertLess(response.content.index(b'name="_save"'), response.content.index(b'name="_approve"'))
        self.assertContains(response, 'value="Approve"')
        self.assertContains(response, 'value="Request changes"')
        self.assertContains(response, "Earlier saved comments")

    def test_approve_uses_unsaved_comments_and_the_exact_frozen_revision(self):
        frozen = self.revision.snapshot.copy()
        self.submission.abstract = "Newer unpublished draft"
        self.submission.save()
        note = "Checked the submitted version, including $x^2$."
        with patch("repository_verification.services.require_verified") as verified:
            response = self.client.post(self.url, {"_approve": "Approve", "review_note": note})
        self.assertRedirects(response, self.queue)
        verified.assert_called_once()
        self.revision.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual(self.revision.snapshot, frozen)
        self.assertEqual(self.revision.status, "approved")
        self.assertEqual(self.revision.review_note, note)
        self.assertEqual(self.revision.reviewed_by, self.reviewer)
        self.assertIsNotNone(self.revision.reviewed_at)
        self.assertEqual(self.submission.published_revision_id, self.revision.pk)
        self.assertFalse(NotificationDelivery.objects.exists())
        self.assertIn("published", " ".join(str(m) for m in get_messages(response.wsgi_request)))
        self.assertIn("status", LogEntry.objects.get(object_id=str(self.revision.pk)).get_change_message().lower())

    def test_request_changes_uses_current_comments_and_queues_frozen_contacts_only(self):
        self.submission.collaborators = [{"name": "Other, Person", "email": "new@example.invalid"}]
        self.submission.save()
        note = "Please clarify this proof. <script>privateFeedback()</script>"
        response = self.client.post(self.url, {"_request_changes": "Request changes", "review_note": note, "status": "approved"})
        self.assertRedirects(response, self.queue)
        self.revision.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual(self.revision.status, "rejected")
        self.assertEqual(self.revision.review_note, note)
        self.assertEqual(self.revision.reviewed_by, self.reviewer)
        self.assertIsNone(self.submission.published_revision_id)
        self.assertEqual(NotificationDelivery.objects.filter(revision=self.revision, kind="changes", status="queued").count(), 1)
        self.assertEqual(mail.outbox, [])
        process_notification_queue()  # Test settings use in-memory email only.
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["frozen@example.invalid"])
        self.assertNotIn("new@example.invalid", mail.outbox[0].body)
        self.assertNotIn(note, mail.outbox[0].body)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("submissions:detail", args=[self.submission.pk])), "&lt;script&gt;privateFeedback()&lt;/script&gt;")

    def test_saved_comments_are_used_when_submitted_unchanged(self):
        response = self.client.post(self.url, {"_request_changes": "1", "review_note": self.revision.review_note})
        self.assertEqual(response.status_code, 302)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.review_note, "Earlier saved comments")
        self.assertEqual(self.revision.status, "rejected")

    def test_missing_verification_keeps_comments_in_form_without_saving_or_publishing(self):
        note = "Unsaved <script>feedback()</script>"
        response = self.client.post(self.url, {"_approve": "1", "review_note": note})
        self.assert_bound_note(response, note)
        self.assertContains(response, "&lt;script&gt;feedback()&lt;/script&gt;")
        self.assertNotContains(response, "<script>feedback()</script>")
        self.assert_pending()
        self.assertFalse(LogEntry.objects.exists())

    def test_invalid_frozen_submission_cannot_publish_or_save_comments(self):
        SubmissionRevision.objects.filter(pk=self.revision.pk).update(snapshot={**self.revision.snapshot, "abstract": "Too short"})
        note = "Please inspect this version."
        with patch("repository_verification.services.require_verified"):
            response = self.client.post(self.url, {"_approve": "1", "review_note": note})
        self.assert_bound_note(response, note)
        self.assertContains(response, "incomplete or invalid")
        self.assert_pending()

    def test_changed_repository_cannot_publish(self):
        self.submission.repository_url = "https://github.com/example/moved"
        self.submission.save()
        note = "Repository changed since submission."
        response = self.client.post(self.url, {"_approve": "1", "review_note": note})
        self.assert_bound_note(response, note)
        self.assertContains(response, "repository changed")
        self.assert_pending()

    def test_newer_publication_cannot_be_replaced_by_an_older_revision(self):
        newer = SubmissionRevision.objects.create(submission=self.submission, number=2, snapshot=self.submission.snapshot(), status="approved")
        self.submission.published_revision = newer
        self.submission.save()
        response = self.client.post(self.url, {"_approve": "1", "review_note": "Trying the earlier revision"})
        self.assert_bound_note(response, "Trying the earlier revision")
        self.assertContains(response, "newer revision is already published")
        self.revision.refresh_from_db()
        self.submission.refresh_from_db()
        self.assertEqual(self.revision.status, "pending")
        self.assertEqual(self.revision.review_note, "Earlier saved comments")
        self.assertEqual(self.submission.published_revision_id, newer.pk)

    def test_invalid_long_comments_block_both_decisions_before_review(self):
        for action in ("_approve", "_request_changes"):
            with self.subTest(action=action), patch.object(SubmissionRevision, "review") as review:
                response = self.client.post(self.url, {action: "1", "review_note": "x" * 3001})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["adminform"].form["review_note"].value(), "x" * 3001)
                self.assertIn("review_note", response.context["adminform"].form.errors)
                review.assert_not_called()
                self.assert_pending()

    def test_ambiguous_actions_do_not_review_or_save_comments(self):
        for extra in ({"_approve": "1", "_request_changes": "1"}, {"_approve": "1", "_save": "1"}, {"_request_changes": "1", "_continue": "1"}):
            with self.subTest(extra=extra):
                response = self.client.post(self.url, {"review_note": "Do not lose this", **extra})
                self.assert_bound_note(response, "Do not lose this")
                self.assertContains(response, "Choose one action")
                self.assert_pending()

    def test_unverified_and_final_revisions_have_no_decision_controls_and_reject_posts(self):
        for status in ("unverified", "approved", "rejected", "superseded"):
            with self.subTest(status=status):
                SubmissionRevision.objects.filter(pk=self.revision.pk).update(status=status)
                response = self.client.get(self.url)
                self.assertNotContains(response, 'name="_approve"')
                self.assertNotContains(response, 'name="_request_changes"')
                for action in ("_approve", "_request_changes"):
                    response = self.client.post(self.url, {action: "1", "review_note": "Preserve attempted comments"})
                    self.assert_bound_note(response, "Preserve attempted comments")
                    self.revision.refresh_from_db()
                    self.assertEqual(self.revision.status, status)
                    self.assertEqual(self.revision.review_note, "Earlier saved comments")
                    self.assertFalse(NotificationDelivery.objects.exists())

    def test_repeated_decision_cannot_overwrite_comments_or_queue_duplicate_notices(self):
        self.client.post(self.url, {"_request_changes": "1", "review_note": "First decision"})
        response = self.client.post(self.url, {"_request_changes": "1", "review_note": "Second attempt"})
        self.assert_bound_note(response, "Second attempt")
        self.assertContains(response, "already been moderated")
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.review_note, "First decision")
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(LogEntry.objects.count(), 1)

    def test_normal_save_and_continue_keep_the_revision_pending(self):
        for action in ("_save", "_continue"):
            with self.subTest(action=action), patch.object(SubmissionRevision, "review") as review:
                response = self.client.post(self.url, {action: "1", "review_note": action + " comments"})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, self.url if action == "_continue" else self.queue)
                self.revision.refresh_from_db()
                self.assertEqual(self.revision.review_note, action + " comments")
                self.assertEqual(self.revision.status, "pending")
                self.assertIsNone(self.revision.reviewed_by)
                self.assertIsNone(self.revision.reviewed_at)
                self.assertFalse(NotificationDelivery.objects.exists())
                review.assert_not_called()

    def test_normal_save_on_completed_revision_does_not_change_final_comments(self):
        self.revision.review(self.reviewer, approve=False, note="Final comments")
        response = self.client.post(self.url, {"_save": "1", "review_note": "Overwrite attempt"})
        self.assertEqual(response.status_code, 302)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "rejected")
        self.assertEqual(self.revision.review_note, "Final comments")

    def test_normal_save_does_not_revert_a_decision_made_after_form_validation(self):
        save_model = SubmissionRevisionAdmin.save_model
        def concurrent_decision(model_admin, request, obj, form, change):
            SubmissionRevision.objects.get(pk=obj.pk).review(self.reviewer, approve=False, note="Concurrent decision")
            return save_model(model_admin, request, obj, form, change)
        # Keep this simulated concurrent decision outside the admin save's
        # rollback by deciding before its transaction, after constructing a form.
        from django.contrib import admin
        request = RequestFactory().post(self.url, {"_save": "1", "review_note": "Stale save comments"})
        request.user = self.reviewer
        model_admin = admin.site._registry[SubmissionRevision]
        form = model_admin.get_form(request, self.revision)(request.POST, instance=self.revision)
        self.assertTrue(form.is_valid(), form.errors)
        obj = model_admin.save_form(request, form, change=True)
        with self.assertRaisesMessage(ValidationError, "already been moderated"):
            concurrent_decision(model_admin, request, obj, form, True)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "rejected")
        self.assertEqual(self.revision.review_note, "Concurrent decision")

    def test_audit_failure_rolls_back_decision_comments_and_notification_outbox(self):
        for action in ("_approve", "_request_changes"):
            with self.subTest(action=action), patch("repository_verification.services.require_verified"):
                with patch.object(SubmissionRevisionAdmin, "log_change", side_effect=ValidationError("Audit could not be recorded")):
                    response = self.client.post(self.url, {action: "1", "review_note": "Preserve on rollback"})
                self.assert_bound_note(response, "Preserve on rollback")
                self.assertContains(response, "Audit could not be recorded")
                self.assert_pending()
                self.assertFalse(LogEntry.objects.exists())

    def test_get_parameters_cannot_make_a_decision(self):
        self.assertEqual(self.client.get(self.url, {"_approve": "1", "review_note": "Ignored"}).status_code, 200)
        self.assert_pending()

    def test_csrf_is_required_for_decision_buttons(self):
        client = self.login(client=Client(enforce_csrf_checks=True))
        self.assertEqual(client.post(self.url, {"_request_changes": "1", "review_note": "Missing token"}).status_code, 403)
        self.assert_pending()
        page = client.get(self.url)
        token = str(page.context["csrf_token"])
        self.assertEqual(client.post(self.url, {"_request_changes": "1", "review_note": "Valid token", "csrfmiddlewaretoken": token}).status_code, 302)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "rejected")

    def test_view_only_staff_can_read_but_cannot_decide_or_save(self):
        user = get_user_model().objects.create_user(username="individual-view-only", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_submissionrevision"))
        self.login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="_approve"')
        self.assertNotContains(response, 'name="_request_changes"')
        for action in ("_save", "_approve", "_request_changes"):
            self.assertEqual(self.client.post(self.url, {action: "1", "review_note": "Unauthorized"}).status_code, 403)
        self.assert_pending()

    def test_ordinary_staff_without_permissions_and_missing_mfa_cannot_decide(self):
        users = get_user_model().objects
        for user in (self.owner, users.create_user(username="individual-unprivileged-staff", is_staff=True)):
            with self.subTest(user=user.username):
                self.login(user)
                self.assertEqual(self.client.post(self.url, {"_request_changes": "1", "review_note": "Denied"}).status_code, 403)
                self.assert_pending()
        self.login(self.reviewer, mfa=False)
        response = self.client.post(self.url, {"_request_changes": "1", "review_note": "No session MFA"})
        self.assertEqual(response.status_code, 403)
        self.assert_pending()

    def test_missing_revision_cannot_publish_or_create_a_revision(self):
        url = reverse("admin:submissions_submissionrevision_change", args=[999999])
        response = self.client.post(url, {"_approve": "1", "review_note": "No object"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SubmissionRevision.objects.count(), 1)
        self.assert_pending()


@override_settings(DEFAULT_FROM_EMAIL="notice@example.invalid")
class IndividualReviewConcurrencyTests(IndividualReviewFixtures, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_decision_posts_save_only_one_decision_and_notice(self):
        clients = [self.login(client=Client()), self.login(client=Client())]
        start = Barrier(2, timeout=5)

        def decide(index):
            close_old_connections()
            try:
                start.wait()
                response = clients[index].post(self.url, {
                    "_request_changes": "1", "review_note": f"Decision {index}",
                })
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, (0, 1)))
        self.assertEqual(sorted(results), [200, 302])
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "rejected")
        self.assertIn(self.revision.review_note, ("Decision 0", "Decision 1"))
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(LogEntry.objects.count(), 1)
