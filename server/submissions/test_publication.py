"""Publication permission is explicit, versioned and scoped to the abstract."""
from datetime import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from allauth.socialaccount.models import SocialAccount

from .forms import SubmissionForm, review_form
from .models import Submission, SubmissionRevision
from .publication import ABSTRACT_LICENSE, DECLARATION, DECLARATION_VERSION


class PublicationDeclarationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="publication-owner")
        self.client.force_login(self.user)
        self.submission = Submission.objects.create(
            owner=self.user, title="A public mathematical project",
            repository_url="https://github.com/example/research", category="math.PR",
            abstract="A detailed mathematical abstract describing the research question and the current results of this project.",
            collaborators=[{"name": "Ada, Example", "orcid": "https://orcid.org/0000-0002-1825-0097", "email": "ada@example.org"}],
            permission_confirmed=True, license_confirmed=True,
        )

    def submit(self, **extra):
        with patch("repository_verification.services.require_verified"):
            return self.client.post(reverse("submissions:submit", args=[self.submission.pk]), {"revision": "1", **extra})

    def test_saved_permission_alone_does_not_accept_new_declaration(self):
        self.submit()
        self.assertFalse(self.submission.revisions.exists())

    def test_explicit_acceptance_records_exact_declaration_and_private_evidence(self):
        self.submit(publication_confirmed="on")
        revision = self.submission.revisions.get()
        declaration = revision.snapshot["publication_declaration"]
        self.assertEqual(declaration["text"], DECLARATION)
        self.assertEqual(declaration["version"], DECLARATION_VERSION)
        self.assertEqual(declaration["license"], ABSTRACT_LICENSE)
        self.assertIsNotNone(datetime.fromisoformat(declaration["accepted_at"]).tzinfo)
        self.assertEqual(revision.submitted_by, self.user)
        public = revision.public_project()
        self.assertEqual(public["summaryLicense"], ABSTRACT_LICENSE)
        self.assertEqual(public["summaryAttribution"], ["Ada Example"])
        self.assertIs(public["credits"][0]["orcidAuthenticated"], False)
        for value in ("publication_declaration", "accepted_at", "ada@example.org", "publication-owner"):
            self.assertNotIn(value, str(public))

    def test_legacy_revision_does_not_gain_fabricated_acceptance(self):
        revision = SubmissionRevision.objects.create(submission=self.submission, number=1, snapshot=self.submission.snapshot())
        self.assertNotIn("publication_declaration", revision.snapshot)
        self.assertNotIn("summaryLicense", revision.public_project())

    def test_submit_screen_offers_unchecked_declaration_and_license_link(self):
        response = self.client.get(reverse("submissions:edit", args=[self.submission.pk]))
        declaration = response.context["form"]["permission_confirmed"]
        self.assertTrue(declaration.field.required)
        self.assertFalse(declaration.value())
        self.assertContains(response, 'name="permission_confirmed"')
        self.assertContains(response, ABSTRACT_LICENSE)
        self.assertContains(response, "licence is irrevocable")
        self.assertNotContains(response, 'name="publication_confirmed"')


class AuthenticatedContributorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="orcid-contributor")
        self.identity = SocialAccount.objects.create(user=self.user, provider="orcid", uid="0000-0002-1825-0097")
        self.client.force_login(self.user)
        self.data = {
            "title": "Authenticated contributor project", "repository_url": "https://github.com/example/research", "category": "math.PR",
            "abstract": "A detailed mathematical abstract describing the research question and the current results of this project.",
            "collaborators-name": ["Ada, Example", "Ada, Example"],
            "collaborators-orcid": ["wrong-and-ignored", "0000-0001-5109-3700"],
            "collaborators-email": ["ada@example.org", "other@example.org"],
            "collaborators-own-orcid": ["1"], "permission_confirmed": "on", "revision": "1",
        }

    def save_draft(self):
        form = SubmissionForm(data=self.data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        draft = form.save(commit=False)
        draft.owner = self.user
        draft.save()
        return draft

    def test_selected_own_id_comes_from_authenticated_account_not_form_text(self):
        draft = self.save_draft()
        own, coauthor = draft.collaborators
        self.assertEqual(own["orcid"], "https://orcid.org/0000-0002-1825-0097")
        self.assertEqual(own["orcid_authentication"]["provider"], "orcid")
        self.assertNotIn("orcid_authentication", coauthor)
        # Matching names confer no identity or authentication on the coauthor.
        with patch("repository_verification.services.require_verified"):
            self.client.post(reverse("submissions:submit", args=[draft.pk]), {"revision": "1", "publication_confirmed": "on"})
        revision = draft.revisions.get()
        self.assertEqual([p["orcidAuthenticated"] for p in revision.public_project()["credits"]], [True, False])
        self.assertNotIn("orcid_authentication", str(revision.public_project()))
        self.assertNotIn("authenticated_at", str(revision.public_project()))

    def test_missing_or_changed_connection_requires_editing_before_submission(self):
        draft = self.save_draft()
        self.identity.uid = "0000-0001-5109-3700"
        self.identity.save()
        form = review_form(draft, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("connected ORCID changed", str(form.errors))
        self.identity.delete()
        form = review_form(draft, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("connect your ORCID account", str(form.errors))

    def test_a_posted_authentication_flag_cannot_authenticate_a_manual_id(self):
        data = {**self.data, "collaborators-own-orcid": [], "collaborators-orcid": ["0000-0001-5109-3700", ""],
                "collaborators-name": ["Ben, Example", ""], "collaborators-email": ["ben@example.org", ""],
                "collaborators-orcidAuthenticated": ["true"], "orcid_authentication": {"provider": "orcid"}}
        form = SubmissionForm(data=data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("orcid_authentication", form.cleaned_data["collaborators"][0])

    def test_own_selection_requires_connection_and_cannot_be_used_twice(self):
        other = get_user_model().objects.create_user(username="no-orcid")
        form = SubmissionForm(data=self.data, user=other)
        self.assertFalse(form.is_valid())
        self.assertIn("connect your ORCID account", str(form.errors))
        form = SubmissionForm(data={**self.data, "collaborators-own-orcid": ["1", "2"]}, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("ORCID is already listed", str(form.errors))

    def test_connected_own_id_cannot_be_manually_retyped_as_unauthenticated(self):
        data = {**self.data, "collaborators-own-orcid": [], "collaborators-orcid": [self.identity.uid, "0000-0001-5109-3700"]}
        form = SubmissionForm(data=data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("collect your own iD", str(form.errors))

    def test_selected_own_checkbox_survives_edit_and_is_not_offered_without_connection(self):
        draft = self.save_draft()
        response = self.client.get(reverse("submissions:edit", args=[draft.pk]))
        self.assertContains(response, 'value="1" data-own-orcid="https://orcid.org/0000-0002-1825-0097" checked')
        self.assertNotContains(response, "connect your ORCID account")
        self.identity.delete()
        response = self.client.get(reverse("submissions:edit", args=[draft.pk]))
        self.assertNotContains(response, 'data-own-orcid=')
        self.assertContains(response, "connect your ORCID account")
