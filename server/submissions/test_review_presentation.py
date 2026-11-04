"""The private review summary reads only the frozen snapshot and escapes it."""
from copy import deepcopy
from html import unescape
import json
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models import JSONField, Value
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape
from django.utils import timezone

from repository_verification.models import RepositoryVerification, VerificationAttempt
from .models import SubmissionRevision
from .test_revision_review import IndividualReviewFixtures


class SubmittedDetailsTests(IndividualReviewFixtures, TestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def render_snapshot(self, snapshot):
        stored = Value(None, output_field=JSONField()) if snapshot is None else snapshot
        SubmissionRevision.objects.filter(pk=self.revision.pk).update(snapshot=stored)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        summary = html.split('<div class="revision-review">', 1)[1].split('<details class="revision-review-json">', 1)[0]
        raw = unescape(re.search(r'<details class="revision-review-json">.*?<pre>(.*?)</pre>', html, re.S)[1])
        self.assertEqual(json.loads(raw), snapshot)
        self.revision.refresh_from_db()
        # PostgreSQL JSONB may reorder object keys. The stored frozen object is
        # authoritative; list order and every value must remain exact.
        self.assertEqual(raw, json.dumps(self.revision.snapshot, ensure_ascii=False, indent=2))
        self.assertEqual(self.revision.snapshot, snapshot)
        return response, summary

    def test_all_submitted_fields_keep_exact_values_order_and_duplicates(self):
        snapshot = deepcopy(self.revision.snapshot)
        snapshot.update(
            title="  Géométrie & $x$\nSecond line  ", abstract="Line one $x^2$.\n\nLine two $$x+y$$.",
            category="math.PR", secondary_categories=["math.AP", "old.CATEGORY", "math.AP"],
            collaborators=[
                {"name": "Ada,  Example", "email": "First@Example.invalid", "orcid": "https://orcid.org/0000-0002-1825-0097"},
                {"name": "李, Example", "email": "second@example.invalid", "orcid": "0000-0002-1825-0097"},
                {"name": "Ada,  Example", "email": "First@Example.invalid"},
            ],
            retired_field={"keep": "All unknown fields", "math": "$x$", "accepted": True},
        )
        response, summary = self.render_snapshot(snapshot)
        for text in (snapshot["title"], snapshot["abstract"], "math.PR", "Probability", "math.AP", "Analysis of PDEs", "old.CATEGORY", "李, Example", "First@Example.invalid", "second@example.invalid"):
            self.assertIn(str(escape(text)), summary)
        self.assertEqual(summary.count("Ada,  Example"), 2)
        self.assertEqual(summary.count("First@Example.invalid"), 2)
        self.assertLess(summary.index("First@Example.invalid"), summary.index("second@example.invalid"))
        self.assertEqual(summary.count("math.AP"), 2)
        self.assertIn("Contributors (3)", summary)
        self.assertNotIn("All unknown fields", summary)
        self.assertContains(response, '<details class="revision-review-json">', count=1)
        self.assertContains(response, '<summary>Exact submission JSON</summary>', count=1)
        self.assertContains(response, 'data-abstract', count=1)
        self.assertContains(response, "revision-review.css")
        self.assertContains(response, "abstract-admin.js")
        self.assertNotContains(response, 'field-abstract_preview')

    def test_later_draft_and_account_changes_cannot_fill_or_replace_snapshot_fields(self):
        snapshot = deepcopy(self.revision.snapshot)
        snapshot["collaborators"] = [{"name": "Frozen, Contributor"}]
        self.submission.title = "Different current draft title"
        self.submission.abstract = "Different draft abstract"
        self.submission.category = "math.CO"
        self.submission.secondary_categories = ["math.DG"]
        self.submission.collaborators = [{"name": "Other, Person", "email": "new-private@example.invalid"}]
        self.submission.save()
        self.owner.email = "account-private@example.invalid"
        self.owner.save()
        _, summary = self.render_snapshot(snapshot)
        self.assertIn("Frozen, Contributor", summary)
        self.assertIn("Not supplied", summary)
        for text in (self.submission.title, self.submission.abstract, "math.CO", "math.DG", "Other, Person", "new-private@example.invalid", self.owner.email):
            self.assertNotIn(text, summary)

    def test_all_untrusted_text_and_link_attributes_are_escaped(self):
        payload = '<img src=x onerror="window.reviewXss=1"> & </pre><script>window.reviewXss=2</script>'
        snapshot = {key: payload for key in ("title", "abstract", "category", "repository_url", "reading_url", "doi")}
        snapshot.update(secondary_categories=[payload], collaborators=[{"name": payload, "email": payload, "orcid": payload}])
        response, summary = self.render_snapshot(snapshot)
        self.assertEqual(summary.count(str(escape(payload))), 10)
        self.assertNotContains(response, payload)
        self.assertNotIn('<a href=', summary)
        self.assertNotIn('<img', summary)
        self.assertNotIn('<script', summary)
        self.assertNotIn('style=', summary)
        self.assertIn("script-src 'self'; style-src 'self'", response["Content-Security-Policy"])

    def test_safe_links_are_clickable_and_keep_exact_display_text(self):
        snapshot = deepcopy(self.revision.snapshot)
        snapshot.update(reading_url="https://example.org/page?a=1&b=2", doi="https://doi.org/10.5281/zenodo.123")
        snapshot["collaborators"][0]["orcid"] = "0000-0002-1825-0097"
        _, summary = self.render_snapshot(snapshot)
        self.assertIn('href="https://example.org/page?a=1&amp;b=2"', summary)
        self.assertIn('href="https://orcid.org/0000-0002-1825-0097"', summary)
        self.assertIn('>0000-0002-1825-0097</a>', summary)
        self.assertEqual(summary.count('rel="noopener noreferrer"'), 4)
        self.assertNotIn('mailto:', summary)

    def test_unsafe_and_non_orcid_links_stay_plain_text(self):
        for url in ("javascript:alert(1)", "data:text/html,evil", "//example.org/path", "https://user:pass@example.org/", "https://example.org/%0aevil", "https://example.org/\" onclick=\"evil", "https://127.0.0.1/", "https://example.org/\\evil"):
            with self.subTest(url=url):
                snapshot = {"repository_url": url, "doi": url, "reading_url": url, "collaborators": [{"orcid": url}]}
                _, summary = self.render_snapshot(snapshot)
                self.assertNotIn('<a href=', summary)
                self.assertIn(str(escape(url)), summary)
        _, summary = self.render_snapshot({"collaborators": [{"orcid": "https://example.org/0000-0002-1825-0097"}]})
        self.assertNotIn('<a href=', summary)

    def test_older_missing_fields_are_explicit_and_never_filled_from_draft(self):
        _, summary = self.render_snapshot({"title": "An older revision", "collaborators": [{"name": "Old, Contributor"}]})
        self.assertIn("An older revision", summary)
        self.assertIn("Old, Contributor", summary)
        self.assertIn("None supplied", summary)
        self.assertIn("Not supplied", summary)
        self.assertNotIn(self.submission.abstract, summary)
        self.assertNotIn("frozen@example.invalid", summary)

    def test_malformed_containers_and_scalar_fields_do_not_break_review(self):
        for snapshot in (
            None, [], "old raw value", 17, False,
            {"title": ["bad"], "abstract": {"bad": True}, "category": 12, "secondary_categories": "math.AP", "collaborators": {}},
            {"repository_url": [], "doi": False, "reading_url": {"bad": True}, "secondary_categories": [{"bad": True}, None, "unknown"], "collaborators": ["legacy", None, 1, [], {"name": {}, "email": [], "orcid": True}, {"name": "Valid, Person"}]},
        ):
            with self.subTest(snapshot=snapshot):
                response, summary = self.render_snapshot(snapshot)
                self.assertIn("exact submission JSON below", summary)
                self.assertContains(response, 'name="_request_changes"')
                self.assertNotIn('<a href=', summary)

    def test_non_object_snapshot_with_verification_evidence_still_renders(self):
        now = timezone.now()
        values = dict(submission=self.submission, user=self.owner, method="file", repository_url=self.submission.repository_url, repository_id=1, repository_owner_id=2, full_name="example/research")
        attempt = VerificationAttempt.objects.create(**values, token_hash="a" * 64, expires_at=now)
        RepositoryVerification.objects.create(**values, attempt=attempt, commit_sha="a" * 40, valid_until=now)
        response, _ = self.render_snapshot(["older", "snapshot"])
        self.assertContains(response, "different repository URL from the proof")

    def test_view_permission_keeps_private_summary_read_only(self):
        user = get_user_model().objects.create_user(username="summary-view-only", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_submissionrevision"))
        self.login(user)
        response, summary = self.render_snapshot(self.revision.snapshot)
        self.assertIn("frozen@example.invalid", summary)
        for action in ("_save", "_approve", "_request_changes"):
            self.assertNotContains(response, f'name="{action}"')
            self.assertEqual(self.client.post(self.url, {action: "1", "review_note": "No permission"}).status_code, 403)
        self.assert_pending()

    def test_private_snapshot_is_unavailable_without_staff_permission_and_mfa(self):
        unauthorized_staff = get_user_model().objects.create_user(username="summary-no-permission", is_staff=True)
        for user, mfa in ((None, False), (self.owner, False), (unauthorized_staff, True), (self.reviewer, False)):
            with self.subTest(user=user, mfa=mfa):
                self.client.logout()
                if user:
                    self.login(user, mfa=mfa)
                response = self.client.get(self.url)
                self.assertIn(response.status_code, (302, 403))
                self.assertNotContains(response, "frozen@example.invalid", status_code=response.status_code)
                self.assertNotContains(response, "revision-review-json", status_code=response.status_code)

    def test_private_emails_do_not_appear_in_public_archive_after_approval(self):
        with patch("repository_verification.services.require_verified"):
            self.revision.review(self.reviewer, approve=True)
        self.client.logout()
        response = self.client.get(reverse("archive_json"))
        self.assertContains(response, self.submission.title)
        self.assertNotContains(response, "frozen@example.invalid")
        self.assertNotContains(response, "review_note")

    def test_get_and_comment_save_cannot_change_snapshot_even_with_forged_fields(self):
        frozen = deepcopy(self.revision.snapshot)
        response = self.client.get(self.url)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        response = self.client.post(self.url, {"_continue": "1", "review_note": "Read frozen fields", "snapshot": '{"title": "Forged"}', "title": "Forged"})
        self.assertEqual(response.status_code, 302)
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.snapshot, frozen)
        self.assertEqual(self.revision.status, "pending")
