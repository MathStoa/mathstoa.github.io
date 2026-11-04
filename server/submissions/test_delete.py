"""Owner-confirmed deletion, authorization and complete record cleanup."""
import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from repository_verification.models import RepositoryVerification, VerificationAttempt
from .models import NotificationDelivery, Submission, SubmissionRevision


class DeleteSubmissionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive_path = Path(self.directory.name) / "projects.json"
        self.archive_path.write_text(json.dumps({"version": 1, "projects": []}))
        override = override_settings(ARCHIVE_PATH=self.archive_path)
        override.enable()
        self.addCleanup(override.disable)
        users = get_user_model()
        self.owner = users.objects.create_user(username="delete-owner", email="owner@example.invalid")
        self.other = users.objects.create_user(username="delete-other", email="other@example.invalid")
        self.staff = users.objects.create_superuser(username="delete-staff", email="staff@example.invalid", password="test-only")
        self.submission = Submission.objects.create(
            owner=self.owner, title="Private deletion target", abstract="Private mathematical abstract",
            repository_url="https://github.com/example/deletion-target", category="math.NT",
            collaborators=[{"name": "Private, Contributor", "email": self.other.email}],
        )
        self.url = reverse("submissions:delete", args=[self.submission.pk])
        self.client.force_login(self.owner)

    def payload(self):
        return {
            "revision": str(self.submission.revision),
            "published_revision": str(self.submission.published_revision_id or ""),
        }

    def add_revision(self, *, status="pending", number=1):
        return SubmissionRevision.objects.create(
            submission=self.submission, number=number, snapshot=self.submission.snapshot(),
            submitted_by=self.owner, status=status,
            reviewed_by=self.staff if status in {"approved", "rejected"} else None,
            reviewed_at=timezone.now() if status in {"approved", "rejected"} else None,
            review_note="Private moderation note" if status == "rejected" else "",
        )

    def publish(self):
        revision = self.add_revision(status="approved")
        self.submission.published_revision = revision
        self.submission.save(update_fields=["published_revision"])
        return revision

    def add_verification(self):
        attempt = VerificationAttempt.objects.create(
            submission=self.submission, user=self.owner, method="file",
            repository_url=self.submission.repository_url, repository_id=1,
            repository_owner_id=2, full_name="example/deletion-target", token_hash="a" * 64,
            expires_at=timezone.now() + timedelta(days=1), consumed_at=timezone.now(),
        )
        proof = RepositoryVerification.objects.create(
            attempt=attempt, submission=self.submission, user=self.owner, method="file",
            repository_url=self.submission.repository_url, repository_id=1,
            repository_owner_id=2, full_name="example/deletion-target",
            valid_until=timezone.now() + timedelta(days=30),
        )
        return attempt, proof

    def test_detail_offers_delete_and_confirmation_get_does_not_mutate(self):
        revision = self.publish()
        attempt, proof = self.add_verification()
        self.assertContains(self.client.get(reverse("submissions:detail", args=[self.submission.pk])), self.url)
        response = self.client.get(self.url)
        self.assertContains(response, self.submission.title)
        self.assertContains(response, 'name="revision" value="1"')
        self.assertContains(response, f'name="published_revision" value="{revision.pk}"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.published_revision_id, revision.pk)
        self.assertTrue(SubmissionRevision.objects.filter(pk=revision.pk).exists())
        self.assertTrue(VerificationAttempt.objects.filter(pk=attempt.pk).exists())
        self.assertTrue(RepositoryVerification.objects.filter(pk=proof.pk).exists())

    def test_owner_can_delete_an_incomplete_submission(self):
        response = self.client.post(self.url, self.payload())
        self.assertRedirects(response, reverse("submissions:list"))
        self.assertFalse(Submission.objects.filter(pk=self.submission.pk).exists())
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_owner_can_delete_each_unpublished_revision_state(self):
        for status in ("unverified", "pending", "rejected", "superseded"):
            with self.subTest(status=status):
                submission = Submission.objects.create(owner=self.owner, title=f"Revision state {status}")
                revision = SubmissionRevision.objects.create(
                    submission=submission, number=1, snapshot=submission.snapshot(), status=status,
                )
                response = self.client.post(reverse("submissions:delete", args=[submission.pk]), {
                    "revision": "1", "published_revision": "",
                })
                self.assertEqual(response.status_code, 302)
                self.assertFalse(Submission.objects.filter(pk=submission.pk).exists())
                self.assertFalse(SubmissionRevision.objects.filter(pk=revision.pk).exists())

    def test_published_deletion_cascades_private_records_and_redacts_matching_logs(self):
        revision = self.publish()
        self.add_revision(status="pending", number=2)
        self.submission.revision = 2
        self.submission.save(update_fields=["revision"])
        attempt, proof = self.add_verification()
        notice = NotificationDelivery.objects.create(
            revision=revision, account=self.owner, kind="created", recipient_digest="b" * 64,
        )
        unrelated = Submission.objects.create(owner=self.owner, title="Keep this project")
        unrelated_revision = SubmissionRevision.objects.create(
            submission=unrelated, number=1, snapshot=unrelated.snapshot(),
        )
        unrelated_notice = NotificationDelivery.objects.create(
            revision=unrelated_revision, account=self.owner, kind="created", recipient_digest="c" * 64,
        )
        entries = []
        for obj in (self.submission, revision, attempt, proof, unrelated):
            entries.append(LogEntry.objects.create(
                user=self.staff, content_type=ContentType.objects.get_for_model(obj),
                object_id=str(obj.pk), object_repr=self.submission.title,
                action_flag=CHANGE, change_message="Private moderation note",
            ))
        self.assertContains(self.client.get(reverse("archive_json")), self.submission.title)
        response = self.client.post(self.url, self.payload())
        self.assertRedirects(response, reverse("submissions:list"))
        self.assertFalse(Submission.objects.filter(pk=self.submission.pk).exists())
        self.assertFalse(SubmissionRevision.objects.filter(submission_id=self.submission.pk).exists())
        self.assertFalse(VerificationAttempt.objects.filter(pk=attempt.pk).exists())
        self.assertFalse(RepositoryVerification.objects.filter(pk=proof.pk).exists())
        self.assertFalse(NotificationDelivery.objects.filter(pk=notice.pk).exists())
        self.assertNotContains(self.client.get(reverse("archive_json")), self.submission.title)
        self.assertTrue(Submission.objects.filter(pk=unrelated.pk).exists())
        self.assertTrue(SubmissionRevision.objects.filter(pk=unrelated_revision.pk).exists())
        self.assertTrue(NotificationDelivery.objects.filter(pk=unrelated_notice.pk).exists())
        self.assertEqual(get_user_model().objects.filter(pk__in=[self.owner.pk, self.other.pk, self.staff.pk]).count(), 3)
        for entry in entries[:-1]:
            entry.refresh_from_db()
            self.assertEqual((entry.object_repr, entry.change_message), ("[erased]", ""))
        entries[-1].refresh_from_db()
        self.assertEqual(entries[-1].change_message, "Private moderation note")

    def test_anonymous_requests_require_sign_in(self):
        self.client.logout()
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                response = method(self.url, self.payload())
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("account_login"), response.url)
                self.assertTrue(Submission.objects.filter(pk=self.submission.pk).exists())

    def test_other_contributor_and_staff_cannot_delete_or_read_confirmation(self):
        for user in (self.other, self.staff):
            self.client.force_login(user)
            for method in (self.client.get, self.client.post):
                with self.subTest(user=user.username, method=method.__name__):
                    response = method(self.url, self.payload())
                    self.assertEqual(response.status_code, 404)
                    self.assertNotIn(self.submission.title, response.content.decode())
                    self.assertTrue(Submission.objects.filter(pk=self.submission.pk).exists())

    def test_missing_submission_returns_not_found(self):
        url = reverse("submissions:delete", args=[uuid4()])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, self.payload()).status_code, 404)

    def test_only_get_and_post_are_allowed(self):
        for method in ("PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                self.assertEqual(self.client.generic(method, self.url).status_code, 405)
                self.assertTrue(Submission.objects.filter(pk=self.submission.pk).exists())

    def test_post_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.url, self.payload()).status_code, 403)
        self.assertTrue(Submission.objects.filter(pk=self.submission.pk).exists())
        self.assertEqual(client.get(self.url).status_code, 200)
        token = client.cookies[settings.CSRF_COOKIE_NAME].value
        response = client.post(self.url, {**self.payload(), "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Submission.objects.filter(pk=self.submission.pk).exists())

    def test_missing_or_invalid_confirmation_state_does_not_delete(self):
        for payload in ({}, {"revision": "1"}, {"published_revision": ""},
                        {"revision": "invalid", "published_revision": ""},
                        {"revision": "1", "published_revision": "invalid"}):
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload)
                self.assertEqual(response.status_code, 409)
                self.assertContains(response, self.submission.title, status_code=409)
                self.assertTrue(Submission.objects.filter(pk=self.submission.pk).exists())

    def test_edit_since_confirmation_requires_a_new_confirmation(self):
        payload = self.payload()
        Submission.objects.filter(pk=self.submission.pk).update(revision=2)
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'name="revision" value="2"', status_code=409)
        self.assertTrue(Submission.objects.filter(pk=self.submission.pk).exists())

    def test_publication_since_confirmation_requires_a_new_confirmation(self):
        payload = self.payload()
        revision = self.publish()
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 409)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.published_revision_id, revision.pk)

    def test_unpublication_since_confirmation_requires_a_new_confirmation(self):
        self.publish()
        payload = self.payload()
        Submission.objects.filter(pk=self.submission.pk).update(published_revision=None)
        self.assertEqual(self.client.post(self.url, payload).status_code, 409)
        self.assertTrue(Submission.objects.filter(pk=self.submission.pk).exists())

    def test_static_archive_duplicate_prevents_partial_deletion(self):
        content = json.dumps({"version": 1, "projects": [{"id": self.submission.public_id}]})
        self.archive_path.write_text(content)
        confirmation = self.client.get(self.url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn("contact", confirmation.content.decode().lower())
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Submission.objects.filter(pk=self.submission.pk).exists())
        self.assertEqual(self.archive_path.read_text(), content)

    def test_deletion_clears_only_its_verification_session_state(self):
        key = f"mathstoa_verification_file:{self.owner.pk}:{self.submission.pk}"
        unrelated_key = f"mathstoa_verification_file:{self.owner.pk}:{uuid4()}"
        session = self.client.session
        session[key] = {"attempt_id": "old-attempt", "content": "private verification file"}
        session[unrelated_key] = {"content": "retain another project's challenge"}
        session.save()
        self.assertEqual(self.client.post(self.url, self.payload()).status_code, 302)
        self.assertNotIn(key, self.client.session)
        self.assertIn(unrelated_key, self.client.session)

    def test_restore_record_logs_uuid_without_submission_or_account_details(self):
        with self.assertLogs("submissions.erasure", level="WARNING") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        self.assertIn(str(self.submission.pk), message)
        for private in (self.submission.title, self.submission.abstract, self.submission.repository_url,
                        self.owner.email, self.other.email, self.owner.username, "Private, Contributor"):
            self.assertNotIn(private, message)
