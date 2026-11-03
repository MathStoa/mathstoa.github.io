"""Authorisation, review integrity and privacy tests; no live provider calls."""
from datetime import timedelta
import sys
from types import ModuleType
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import DOI_GUIDE_URL, ORCID_REGISTER_URL, SubmissionForm, normalize_doi, normalize_orcid
from .models import Submission, SubmissionRevision, display_name


class SubmissionTests(TestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model()
        self.owner = users.objects.create_user(username="owner", email="private-owner@example.org")
        self.other = users.objects.create_user(username="other", email="private-other@example.org")
        self.staff = users.objects.create_superuser(username="reviewer", email="private-reviewer@example.org", password="test-only")
        self.client.force_login(self.owner)
        # Ownership proof is tested by its own module. Unit tests isolate its boundary.
        verification = ModuleType("repository_verification.services")
        verification.require_verified = Mock(return_value=None)
        verification.has_current_verification = Mock(return_value=True)
        self.verification = verification.require_verified
        self.verification_patch = patch.dict(sys.modules, {"repository_verification.services": verification})
        self.verification_patch.start()
        self.addCleanup(self.verification_patch.stop)
        self.payload = {
            "title": "Probability research notes", "repository_url": "https://github.com/example/research",
            "doi": "10.5281/zenodo.1234567",
            "reading_url": "https://example.org/research", "abstract": "These research notes explore a mathematical question, explain the current partial results, and describe useful future work.",
            "category": "math.PR", "collaborators-name": "Anna, Example", "collaborators-orcid": "0000-0002-1825-0097", "collaborators-email": "anna@university.example",
            "permission_confirmed": "on", "revision": "1",
        }

    def create_submission(self, payload=None):
        response = self.client.post(reverse("submissions:create"), payload or self.payload)
        self.assertEqual(response.status_code, 302)
        return Submission.objects.latest("created_at")

    def submit(self, submission):
        response = self.client.post(reverse("submissions:submit", args=[submission.pk]), {"revision": submission.revision})
        self.assertEqual(response.status_code, 302)
        return submission.revisions.get(number=submission.revision)

    def test_draft_page_offers_submission_then_states_the_outcome_without_mentioning_review(self):
        submission = self.create_submission()
        url = reverse("submissions:detail", args=[submission.pk])
        pages = [self.client.get(url)]
        self.assertContains(pages[0], "Submit saved draft")
        self.submit(submission)
        pages.append(self.client.get(url))
        self.assertContains(pages[1], "The repository details have been submitted and will undergo moderation before being made public.", count=1)
        self.assertNotContains(pages[1], "Submit saved draft")
        self.assertNotContains(pages[1], "Repository access is verified")
        submission.revisions.get().review(self.staff, approve=False, note="Please expand the abstract.")
        pages.append(self.client.get(url))
        self.assertContains(pages[2], "Changes were requested for this version.")
        self.assertNotContains(pages[2], "Submit saved draft")
        self.client.post(reverse("submissions:edit", args=[submission.pk]), self.payload)
        pages.append(self.client.get(url))
        self.assertContains(pages[3], "Submit saved draft")
        for page in pages:
            text = page.content.decode().lower()
            self.assertNotIn("review", text)
            self.assertNotIn("editorial", text)

    def test_review_needs_first_names_and_surname_separated_by_a_comma(self):
        for name in ("Anna Example", "Anna, ", ", Example", "Anna, Maria, Example"):
            with self.subTest(name=name):
                form = SubmissionForm({**self.payload, "collaborators-name": name}, for_submission=True)
                self.assertEqual(form.errors["collaborators"], ["Contributor 1: write the name as first names, a comma, then the surname."])
        self.assertTrue(SubmissionForm({**self.payload, "collaborators-name": "Anna Example"}).is_valid())
        form = SubmissionForm({**self.payload, "collaborators-name": "Anna  Maria ,Example"}, for_submission=True)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["collaborators"][0]["name"], "Anna  Maria, Example")
        self.assertEqual(display_name("Anna Maria, Example"), "Anna Maria Example")
        self.assertEqual(display_name("Anna"), "Anna")

    def test_review_needs_a_valid_email_for_each_contributor_and_keeps_it_private(self):
        rows = {"collaborators-name": ["Anna, Example", "Ben, Example"], "collaborators-orcid": ["0000-0002-1825-0097", "0000-0001-5109-3700"]}
        self.assertTrue(SubmissionForm({**self.payload, **rows, "collaborators-email": ["anna@university.example", ""]}).is_valid())
        form = SubmissionForm({**self.payload, **rows, "collaborators-email": ["anna@university.example", ""]}, for_submission=True)
        self.assertEqual(form.errors["collaborators"], ["Contributor 2: add their institutional email."])
        form = SubmissionForm({**self.payload, **rows, "collaborators-email": ["anna@university.example", "not-an-address"]})
        self.assertEqual(form.errors["collaborators"], ["Contributor 2: enter a valid email address."])
        submission = self.create_submission()
        self.assertContains(self.client.get(reverse("submissions:detail", args=[submission.pk])), "Email (private): anna@university.example")
        self.assertContains(self.client.get(reverse("submissions:edit", args=[submission.pk])), 'value="anna@university.example"')
        self.submit(submission).review(self.staff, approve=True)
        self.assertNotContains(self.client.get("/data/projects.json"), "anna@university.example")
        self.assertNotContains(self.client.get("/feed.xml"), "anna@university.example")

    def test_anonymous_users_must_sign_in(self):
        self.client.logout()
        for route in ("submissions:list", "submissions:create"):
            self.assertEqual(self.client.get(reverse(route)).status_code, 302)
        self.assertEqual(self.client.post(reverse("submissions:create"), self.payload).status_code, 302)
        self.assertFalse(Submission.objects.exists())

    def test_owner_is_assigned_from_session_not_posted_fields(self):
        submission = self.create_submission({**self.payload, "owner": self.other.pk, "published_revision": "1", "revision": "999"})
        self.assertEqual(submission.owner, self.owner)
        self.assertEqual(submission.revision, 1)
        self.assertIsNone(submission.published_revision)

    def test_owner_can_return_and_edit(self):
        submission = self.create_submission()
        self.assertContains(self.client.get(reverse("submissions:list")), submission.title)
        self.assertEqual(self.client.get(reverse("submissions:detail", args=[submission.pk])).status_code, 200)
        response = self.client.post(reverse("submissions:edit", args=[submission.pk]), {**self.payload, "title": "Updated research notes"})
        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        self.assertEqual(submission.title, "Updated research notes")
        self.assertEqual(submission.revision, 2)

    def test_others_cannot_read_or_change_submission_even_if_listed_as_contributor(self):
        submission = self.create_submission({**self.payload, "collaborators-name": "other", "collaborators-orcid": ""})
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse("submissions:list")), submission.title)
        for name in ("detail", "edit"):
            self.assertEqual(self.client.get(reverse("submissions:" + name, args=[submission.pk])).status_code, 404)
        for name, payload in (("edit", self.payload), ("submit", {"revision": 1})):
            self.assertEqual(self.client.post(reverse("submissions:" + name, args=[submission.pk]), payload).status_code, 404)
        submission.refresh_from_db()
        self.assertEqual(submission.revision, 1)

    def test_stale_edits_do_not_overwrite_newer_draft(self):
        submission = self.create_submission()
        self.client.post(reverse("submissions:edit", args=[submission.pk]), {**self.payload, "title": "First update"})
        response = self.client.post(reverse("submissions:edit", args=[submission.pk]), {**self.payload, "title": "Stale update"})
        self.assertEqual(response.status_code, 409)
        submission.refresh_from_db()
        self.assertEqual(submission.title, "First update")

    def test_draft_can_be_incomplete_but_cannot_be_submitted(self):
        submission = self.create_submission({"title": "Early draft", "revision": "1"})
        response = self.client.post(reverse("submissions:submit", args=[submission.pk]), {"revision": 1})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(submission.revisions.exists())

    def test_form_marks_the_doi_and_website_optional_and_links_to_the_doi_guide(self):
        response = self.client.get(reverse("submissions:create"))
        self.assertContains(response, "DOI <span>(optional)</span>")
        self.assertContains(response, "Project website <span>(optional)</span>")
        self.assertContains(response, "Further subjects <span>(optional)</span>")
        self.assertContains(response, "(optional)", count=3)
        self.assertContains(response, 'placeholder="Induced subgraphs of hypercubes and a proof of the Sensitivity Conjecture"')
        self.assertNotContains(response, "Required to save a draft")
        self.assertNotContains(response, "optional in a draft")
        self.assertContains(response, DOI_GUIDE_URL)
        self.assertContains(response, f'<a href="{ORCID_REGISTER_URL}"')
        self.assertContains(response, 'name="collaborators-name"')
        self.assertTrue(SubmissionForm({**self.payload, "doi": "", "reading_url": ""}, for_submission=True).is_valid())
        for field in ("repository_url", "abstract", "category", "collaborators"):
            with self.subTest(field=field):
                form = SubmissionForm({key: value for key, value in self.payload.items() if not key.startswith(field)}, for_submission=True)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_each_contributor_needs_an_orcid_for_review_but_not_for_a_draft(self):
        rows = {"collaborators-name": ["Anna, Example", "Ben, Example"], "collaborators-orcid": ["0000-0002-1825-0097", ""], "collaborators-email": ["anna@university.example", "ben@university.example"]}
        self.assertTrue(SubmissionForm({**self.payload, **rows}).is_valid())
        form = SubmissionForm({**self.payload, **rows}, for_submission=True)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["collaborators"], ["Contributor 2: add their ORCID iD."])
        submission = self.create_submission({**self.payload, **rows})
        self.assertContains(self.client.get(reverse("submissions:detail", args=[submission.pk])), "ORCID iD not supplied yet")
        response = self.client.post(reverse("submissions:submit", args=[submission.pk]), {"revision": 1})
        self.assertContains(response, "Contributor 2: add their ORCID iD.", status_code=400)
        self.assertFalse(submission.revisions.exists())

    def test_doi_is_stored_as_a_link_and_shown_on_the_draft(self):
        for value in ("10.5281/zenodo.1234567", "doi:10.5281/zenodo.1234567", "https://doi.org/10.5281/zenodo.1234567", "http://dx.doi.org/10.5281/zenodo.1234567"):
            with self.subTest(value=value):
                self.assertEqual(normalize_doi(value), "https://doi.org/10.5281/zenodo.1234567")
        for value in ("https://zenodo.org/records/1234567", "10.123/short-prefix", "10.5281/zenodo 1", "https://doi.org/", "10.5281/<script>"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_doi(value)
        submission = self.create_submission()
        self.assertEqual(submission.doi, "https://doi.org/10.5281/zenodo.1234567")
        self.assertContains(self.client.get(reverse("submissions:detail", args=[submission.pk])), 'href="https://doi.org/10.5281/zenodo.1234567"')

    def test_doi_is_optional_for_review_and_publication(self):
        submission = self.create_submission({**self.payload, "doi": ""})
        self.assertContains(self.client.get(reverse("submissions:detail", args=[submission.pk])), "<dt>DOI</dt><dd>None (optional)</dd>")
        self.submit(submission).review(self.staff, approve=True)
        project = next(p for p in self.client.get("/data/projects.json").json()["projects"] if p["id"] == submission.public_id)
        self.assertNotIn("doi", project)
        self.assertEqual(self.client.get("/feed.xml").status_code, 200)

    def test_lean_formalisation_is_a_subject_that_publishes(self):
        self.assertContains(self.client.get(reverse("submissions:create")), '<option value="cs.LO">Lean formalisation</option>')
        submission = self.create_submission({**self.payload, "category": "cs.LO"})
        self.submit(submission).review(self.staff, approve=True)
        project = next(p for p in self.client.get("/data/projects.json").json()["projects"] if p["id"] == submission.public_id)
        self.assertEqual(project["categories"], [{"code": "cs.LO", "label": "Lean formalisation"}])

    def test_further_subjects_are_optional_and_publish_after_the_main_subject(self):
        self.assertContains(self.client.get(reverse("submissions:create")), 'name="secondary_categories" value="math.AP"')
        plain = self.create_submission()
        self.assertEqual(plain.secondary_categories, [])
        self.assertContains(self.client.get(reverse("submissions:detail", args=[plain.pk])), "<dt>Further subjects</dt><dd>None (optional)</dd>")
        # The main subject is listed once, however the author ticks it.
        submission = self.create_submission({**self.payload, "secondary_categories": ["math.AP", "math.PR", "math.FA"]})
        self.assertEqual(submission.secondary_categories, ["math.AP", "math.FA"])
        self.assertContains(self.client.get(reverse("submissions:detail", args=[submission.pk])), "<dt>Further subjects</dt><dd>Analysis of PDEs, Functional analysis</dd>")
        self.assertContains(self.client.get(reverse("submissions:edit", args=[submission.pk])), 'value="math.AP" id="id_secondary_categories_1" checked')
        self.submit(submission).review(self.staff, approve=True)
        project = next(p for p in self.client.get("/data/projects.json").json()["projects"] if p["id"] == submission.public_id)
        self.assertEqual(project["categories"], [{"code": "math.PR", "label": "Probability"}, {"code": "math.AP", "label": "Analysis of PDEs"}, {"code": "math.FA", "label": "Functional analysis"}])
        self.assertContains(self.client.get("/feed.xml"), 'term="math.FA"')

    def test_at_most_three_further_subjects_from_the_known_list(self):
        form = SubmissionForm({**self.payload, "secondary_categories": ["math.AG", "math.AP", "math.AT", "math.CO"]})
        self.assertEqual(form.errors["secondary_categories"], ["Please tick at most 3 further subjects."])
        self.assertTrue(SubmissionForm({**self.payload, "secondary_categories": ["math.AG", "math.AP", "math.AT", "math.PR"]}).is_valid())
        self.assertIn("secondary_categories", SubmissionForm({**self.payload, "secondary_categories": ["math.XX"]}).errors)
        self.assertTrue(SubmissionForm({**self.payload, "secondary_categories": []}, for_submission=True).is_valid())

    def test_revisions_frozen_before_further_subjects_existed_still_publish(self):
        submission = self.create_submission({**self.payload, "secondary_categories": ["math.AP"]})
        revision = self.submit(submission)
        # QuerySet.update bypasses the immutability guard: emulate a snapshot taken before the field existed.
        SubmissionRevision.objects.filter(pk=revision.pk).update(snapshot={key: value for key, value in revision.snapshot.items() if key != "secondary_categories"})
        revision.refresh_from_db()
        revision.review(self.staff, approve=True)
        project = next(p for p in self.client.get("/data/projects.json").json()["projects"] if p["id"] == submission.public_id)
        self.assertEqual(project["categories"], [{"code": "math.PR", "label": "Probability"}])

    def test_website_is_optional_for_review_and_publication(self):
        submission = self.create_submission({**self.payload, "reading_url": ""})
        self.submit(submission).review(self.staff, approve=True)
        project = next(p for p in self.client.get("/data/projects.json").json()["projects"] if p["id"] == submission.public_id)
        self.assertNotIn("readingUrl", project)
        self.assertEqual(project["doi"], "https://doi.org/10.5281/zenodo.1234567")
        self.assertEqual(self.client.get("/feed.xml").status_code, 200)

    def test_contributor_rows_keep_order_skip_blanks_and_can_all_be_removed(self):
        rows = {"collaborators-name": ["Anna", "", "Ben"], "collaborators-orcid": ["https://orcid.org/0000-0002-1825-0097", "", ""]}
        submission = self.create_submission({**self.payload, **rows})
        self.assertEqual(submission.collaborators, [{"name": "Anna", "orcid": "https://orcid.org/0000-0002-1825-0097", "email": "anna@university.example"}, {"name": "Ben", "orcid": "", "email": ""}])
        edit = self.client.get(reverse("submissions:edit", args=[submission.pk]))
        self.assertContains(edit, 'value="Anna"')
        self.assertContains(edit, 'value="0000-0002-1825-0097"')
        without_rows = {key: value for key, value in self.payload.items() if not key.startswith("collaborators")}
        self.assertEqual(self.client.post(reverse("submissions:edit", args=[submission.pk]), without_rows).status_code, 302)
        submission.refresh_from_db()
        self.assertEqual(submission.collaborators, [])
        response = self.client.post(reverse("submissions:submit", args=[submission.pk]), {"revision": submission.revision})
        self.assertContains(response, "Add at least one contributor.", status_code=400)

    def test_revision_submitted_before_the_doi_requirement(self):
        submission = self.create_submission()
        snapshot = {key: value for key, value in submission.snapshot().items() if key != "doi"}
        snapshot.update(kind="Research", stage="Exploration", collaboration="See repository", contribution_note="Old note", license_note="MIT",
                        collaborators=[{"name": "Anna", "role": "Author", "orcid": "", "affiliation": "Example University"}])
        revision = SubmissionRevision.objects.create(submission=submission, number=1, snapshot=snapshot, submitted_by=self.owner)
        with self.assertRaisesRegex(ValidationError, "incomplete or invalid"):
            revision.review(self.staff, approve=True)
        # A listing approved before the change still exports, without the removed fields.
        SubmissionRevision.objects.filter(pk=revision.pk).update(status="approved", reviewed_at=timezone.now())
        Submission.objects.filter(pk=submission.pk).update(published_revision=revision)
        project = next(p for p in self.client.get("/data/projects.json").json()["projects"] if p["id"] == submission.public_id)
        self.assertEqual(project["credits"], [{"name": "Anna"}])
        for removed in ("doi", "kind", "stage", "collaboration", "contributionNote", "licenseNote"):
            self.assertNotIn(removed, project)
        self.assertEqual(self.client.get("/feed.xml").status_code, 200)

    def test_permission_confirmation_required_before_review_and_grants_licence(self):
        payload = {key: value for key, value in self.payload.items() if key != "permission_confirmed"}
        submission = self.create_submission(payload)
        self.assertFalse(submission.license_confirmed)
        response = self.client.post(reverse("submissions:submit", args=[submission.pk]), {"revision": 1})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(submission.revisions.exists())
        self.assertTrue(self.create_submission().license_confirmed)

    def test_stale_submit_does_not_submit_unseen_draft(self):
        submission = self.create_submission()
        self.client.post(reverse("submissions:edit", args=[submission.pk]), self.payload)
        self.client.post(reverse("submissions:submit", args=[submission.pk]), {"revision": 1})
        self.assertFalse(submission.revisions.exists())

    def test_submission_requires_repository_verification(self):
        submission = self.create_submission()
        self.verification.side_effect = ValidationError("Verify this repository first.")
        response = self.client.post(reverse("submissions:submit", args=[submission.pk]), {"revision": 1}, follow=True)
        self.assertContains(response, "Verify this repository first.")
        self.assertFalse(submission.revisions.exists())

    def test_approval_rechecks_repository_verification(self):
        submission = self.create_submission()
        revision = self.submit(submission)
        self.verification.side_effect = ValidationError("Repository access was removed.")
        with self.assertRaises(ValidationError):
            revision.review(self.staff, approve=True)
        submission.refresh_from_db()
        revision.refresh_from_db()
        self.assertIsNone(submission.published_revision)
        self.assertEqual(revision.status, "pending")

    def test_repository_changed_since_snapshot_cannot_be_published(self):
        submission = self.create_submission()
        revision = self.submit(submission)
        submission.repository_url = "https://github.com/example/different"
        submission.save()
        with self.assertRaisesRegex(ValidationError, "repository changed"):
            revision.review(self.staff, approve=True)
        submission.refresh_from_db()
        self.assertIsNone(submission.published_revision)

    def test_review_snapshot_is_immutable_and_draft_stays_separate(self):
        submission = self.create_submission()
        revision = self.submit(submission)
        self.assertEqual(revision.submitted_by, self.owner)
        self.client.post(reverse("submissions:edit", args=[submission.pk]), {**self.payload, "title": "Unreviewed title"})
        revision.refresh_from_db()
        self.assertEqual(revision.snapshot["title"], self.payload["title"])
        revision.review(self.staff, approve=True)
        submission.refresh_from_db()
        self.assertEqual(submission.published_revision.snapshot["title"], self.payload["title"])
        revision.snapshot["title"] = "Changed frozen text"
        with self.assertRaisesRegex(ValidationError, "immutable"):
            revision.save()

    def test_publication_keeps_old_revision_until_new_one_is_approved(self):
        submission = self.create_submission()
        first = self.submit(submission)
        first.review(self.staff, approve=True)
        self.client.post(reverse("submissions:edit", args=[submission.pk]), {**self.payload, "title": "Second version"})
        submission.refresh_from_db()
        second = self.submit(submission)
        self.assertEqual(submission.published_revision_id, first.pk)
        second.review(self.staff, approve=True)
        submission.refresh_from_db()
        self.assertEqual(submission.published_revision_id, second.pk)
        self.assertEqual(first.snapshot["title"], self.payload["title"])

    def test_old_pending_revision_cannot_roll_back_newer_publication(self):
        submission = self.create_submission()
        first = self.submit(submission)
        self.client.post(reverse("submissions:edit", args=[submission.pk]), {**self.payload, "title": "Second version"})
        submission.refresh_from_db()
        second = self.submit(submission)
        second.review(self.staff, approve=True)
        with self.assertRaisesRegex(ValidationError, "newer revision"):
            first.review(self.staff, approve=True)

    def test_nonreviewer_and_staff_without_review_permission_cannot_publish(self):
        submission = self.create_submission()
        revision = self.submit(submission)
        self.other.is_staff = True
        self.other.save()
        for user in (self.owner, self.other):
            with self.assertRaises(ValidationError):
                revision.review(user, approve=True)

    def test_rejection_and_resubmission_keep_review_history(self):
        submission = self.create_submission()
        first = self.submit(submission)
        first.review(self.staff, approve=False, note="Please explain the contribution policy.")
        self.assertIsNone(submission.published_revision)
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))
        self.assertContains(response, "Please explain the contribution policy.")
        self.client.post(reverse("submissions:submit", args=[submission.pk]), {"revision": 1})
        self.assertEqual(submission.revisions.count(), 1)
        self.client.post(reverse("submissions:edit", args=[submission.pk]), self.payload)
        submission.refresh_from_db()
        second = self.submit(submission)
        self.assertEqual(second.number, 2)
        self.assertEqual(submission.revisions.count(), 2)

    def test_pending_drafts_and_private_fields_are_not_exported(self):
        submission = self.create_submission()
        revision = self.submit(submission)
        response = self.client.get("/data/projects.json")
        self.assertNotContains(response, submission.title)
        revision.review(self.staff, approve=True, note="Private editorial note")
        response = self.client.get("/data/projects.json")
        self.assertContains(response, submission.title)
        for secret in (self.owner.email, self.staff.email, "Private editorial note", "token_hash", "submitted_by", "owner_id"):
            self.assertNotContains(response, secret)
        project = next(p for p in response.json()["projects"] if p["id"] == submission.public_id)
        self.assertEqual(project["credits"], [{"name": "Anna Example", "orcid": "https://orcid.org/0000-0002-1825-0097"}])
        self.assertEqual(project["doi"], "https://doi.org/10.5281/zenodo.1234567")
        self.assertEqual(project["readingUrl"], "https://example.org/research")
        self.assertFalse(project["example"])

    def test_existing_example_ids_cannot_be_claimed(self):
        submission = self.create_submission({**self.payload, "id": "mathlib4", "title": "mathlib4"})
        self.assertNotEqual(submission.public_id, "mathlib4")
        self.assertEqual(self.client.get("/submissions/mathlib4/edit/").status_code, 404)

    def test_csrf_blocks_mutations_and_get_does_not_submit(self):
        submission = self.create_submission()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        for name, payload in (("edit", self.payload), ("submit", {"revision": 1})):
            self.assertEqual(client.post(reverse("submissions:" + name, args=[submission.pk]), payload).status_code, 403)
        self.assertEqual(client.get(reverse("submissions:submit", args=[submission.pk])).status_code, 405)
        self.assertFalse(submission.revisions.exists())

    def test_private_pages_are_not_cached(self):
        submission = self.create_submission()
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_detail_page_offers_no_editor_invitations(self):
        submission = self.create_submission()
        response = self.client.get(reverse("submissions:detail", args=[submission.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Editing access")
        self.assertNotContains(response, "invitation")

    def test_unsafe_links_are_rejected_before_storage(self):
        for url in ("javascript:alert(1)", "http://example.org/repo", "https://user:pass@example.org/repo", "https://localhost/repo", "https://127.0.0.1/repo", "https://example.org:444/repo", "https://example.org/%0aevil", "https://example.org\\@evil.org"):
            with self.subTest(url=url):
                form = SubmissionForm({**self.payload, "repository_url": url})
                self.assertFalse(form.is_valid())
                self.assertIn("repository_url", form.errors)

    def test_orcid_validation_checks_structure_and_checksum(self):
        self.assertEqual(normalize_orcid("0000-0002-1825-0097"), "https://orcid.org/0000-0002-1825-0097")
        for value in ("0000-0002-1825-0098", "https://evil.org/0000-0002-1825-0097", "not-an-id"):
            with self.assertRaises(ValidationError):
                normalize_orcid(value)

    def test_repeated_or_invalid_orcid_unnamed_and_oversize_credit_rejected(self):
        for names, orcids in (
            (["A", "B"], ["0000-0002-1825-0097", "https://orcid.org/0000-0002-1825-0097"]),
            (["A"], ["0000-0002-1825-0098"]), (["A" * 201], [""]), ([""], ["0000-0002-1825-0097"]),
        ):
            with self.subTest(names=names, orcids=orcids):
                form = SubmissionForm({**self.payload, "collaborators-name": names, "collaborators-orcid": orcids})
                self.assertFalse(form.is_valid())
                self.assertIn("collaborators", form.errors)

    def test_control_characters_cannot_break_the_public_feed(self):
        for key, field in (("title", "title"), ("abstract", "abstract"), ("collaborators-name", "collaborators")):
            form = SubmissionForm({**self.payload, key: self.payload[key] + "\x01"})
            self.assertFalse(form.is_valid())
            self.assertIn(field, form.errors)


class VerificationBoundaryTests(TestCase):
    """Exercise real submission gates and proof records; mock only host transport."""

    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username="proof-owner")
        self.staff = get_user_model().objects.create_superuser(username="proof-reviewer", email="reviewer@example.org", password="test-only")
        self.client.force_login(self.owner)
        self.submission = Submission.objects.create(
            owner=self.owner, title="Verified repository", repository_url="https://github.com/example/research",
            doi="https://doi.org/10.5281/zenodo.1234567",
            reading_url="https://example.org/research", abstract="A mathematical project with enough explanatory detail to make its research question and current results clear to readers.",
            category="math.PR", collaborators=[{"name": "Ada, Contributor", "orcid": "https://orcid.org/0000-0002-1825-0097", "email": "contributor@university.example"}],
            permission_confirmed=True, license_confirmed=True,
        )

    def make_proof(self):
        from repository_verification.models import RepositoryVerification, VerificationAttempt
        attempt = VerificationAttempt.objects.create(
            submission=self.submission, user=self.owner, method="file", repository_url=self.submission.repository_url,
            repository_id=10, repository_owner_id=20, full_name="example/research", token_hash="a" * 64,
            expires_at=timezone.now() + timedelta(hours=1), consumed_at=timezone.now(),
        )
        return RepositoryVerification.objects.create(
            attempt=attempt, submission=self.submission, user=self.owner, method="file",
            repository_url=self.submission.repository_url, repository_id=10, repository_owner_id=20,
            full_name="example/research", valid_until=timezone.now() + timedelta(days=1),
        )

    def test_real_gate_refuses_missing_proof(self):
        response = self.client.post(reverse("submissions:submit", args=[self.submission.pk]), {"revision": 1}, follow=True)
        self.assertContains(response, "must verify repository authorization")
        self.assertFalse(self.submission.revisions.exists())

    def test_real_proof_allows_review_and_public_export_but_transfer_blocks_next_review(self):
        from repository_verification.github import Repository
        repository = Repository(repository_id=10, owner_id=20, owner_type="User", owner="example", name="research", default_branch="main")
        self.make_proof()
        with patch("repository_verification.services.GitHubClient.resolve_repository", return_value=repository):
            response = self.client.post(reverse("submissions:submit", args=[self.submission.pk]), {"revision": 1})
            self.assertEqual(response.status_code, 302)
            first = self.submission.revisions.get(number=1)
            first.review(self.staff, approve=True)
            self.assertContains(self.client.get("/data/projects.json"), "Verified repository")
            feed = self.client.get("/feed.xml")
            self.assertEqual(feed.status_code, 200)
            self.assertContains(feed, "Verified repository")
            self.submission.refresh_from_db()
            self.submission.revision = 2
            self.submission.title = "Unreviewed changes"
            self.submission.save()
            self.client.post(reverse("submissions:submit", args=[self.submission.pk]), {"revision": 2})
        changed_owner = Repository(repository_id=10, owner_id=99, owner_type="User", owner="example", name="research", default_branch="main")
        with patch("repository_verification.services.GitHubClient.resolve_repository", return_value=changed_owner):
            second = self.submission.revisions.get(number=2)
            with self.assertRaises(ValidationError):
                second.review(self.staff, approve=True)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.published_revision.number, 1)
