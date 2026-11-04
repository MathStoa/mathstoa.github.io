from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from submissions.forms import SubmissionForm
from submissions.models import Submission


class SubmissionMutationLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.owner = users.objects.create_user(username="rate-owner")
        self.other = users.objects.create_user(username="rate-other")
        self.client.force_login(self.owner)
        self.payload = {
            "title": "Research draft", "repository_url": "https://github.com/example/research",
            "doi": "10.5281/zenodo.1234567", "reading_url": "https://example.org/research",
            "abstract": "These mathematical notes explain the research question, its current partial results, and useful work still to be done.",
            "category": "math.PR", "collaborators-name": "Ada, Researcher", "collaborators-orcid": "0000-0002-1825-0097", "collaborators-email": "researcher@university.example", "permission_confirmed": "on", "revision": "1",
        }
        form = SubmissionForm(self.payload)
        self.assertTrue(form.is_valid(), form.errors)
        self.submission = form.save(commit=False)
        self.submission.owner = self.owner
        self.submission.save()

    def submit(self):
        return self.client.post(reverse("submissions:submit", args=[self.submission.pk]), {"revision": self.submission.revision, "publication_confirmed": "on"})

    def new_revision(self):
        self.submission.revision += 1
        self.submission.save(update_fields=["revision"])

    @override_settings(ACCOUNT_RATE_LIMITS={"submission_submit": "1/h/user,1/h/key"})
    def test_repeated_submission_returns_before_external_verification_and_throttle(self):
        with patch("repository_verification.services.require_verified") as verify:
            for _ in range(25):
                self.assertEqual(self.submit().status_code, 302)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(self.submission.revisions.count(), 1)

    def test_unauthorised_users_cannot_trigger_verification_or_consume_project_limit(self):
        self.client.force_login(self.other)
        with patch("repository_verification.services.require_verified") as verify, patch("submissions.views.limit_action") as limited:
            self.assertEqual(self.submit().status_code, 404)
            self.assertEqual(self.client.post(reverse("submissions:edit", args=[self.submission.pk]), self.payload).status_code, 404)
        verify.assert_not_called()
        limited.assert_not_called()

    @override_settings(ACCOUNT_RATE_LIMITS={"submission_submit": "2/h/user,2/h/key"})
    def test_new_revisions_are_limited_before_external_work(self):
        with patch("repository_verification.services.require_verified") as verify:
            self.assertEqual(self.submit().status_code, 302)
            self.new_revision()
            self.assertEqual(self.submit().status_code, 302)
            self.new_revision()
            response = self.submit()
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "3600")
        self.assertEqual(self.submission.revisions.count(), 2)

    @override_settings(ACCOUNT_RATE_LIMITS={"submission_create": "2/5m/user"})
    def test_direct_submission_creation_is_limited_per_user_not_shared_ip(self):
        url = reverse("submissions:create")
        self.assertEqual(self.client.post(url, self.payload).status_code, 302)
        self.assertEqual(self.client.post(url, self.payload).status_code, 302)
        response = self.client.post(url, self.payload)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "300")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(Submission.objects.filter(owner=self.owner).count(), 3)
        self.assertEqual(sum(item.revisions.count() for item in Submission.objects.filter(owner=self.owner)), 2)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, self.payload).status_code, 302)

    @override_settings(ACCOUNT_RATE_LIMITS={"submission_edit": "1/5m/user,1/5m/key"})
    def test_edit_throttle_preserves_latest_submission_and_revision(self):
        url = reverse("submissions:edit", args=[self.submission.pk])
        self.assertEqual(self.client.post(url, {**self.payload, "title": "Saved update"}).status_code, 302)
        response = self.client.post(url, {**self.payload, "revision": "2", "title": "Blocked update"})
        self.assertEqual(response.status_code, 429)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.title, "Saved update")
        self.assertEqual(self.submission.revision, 2)
        self.assertEqual(list(self.submission.revisions.values_list("number", flat=True)), [2])
