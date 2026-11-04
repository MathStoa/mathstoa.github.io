from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from .models import Submission, SubmissionRevision
from .retention_review import retention_candidates, six_months_before


class RetentionReviewTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, 12, tzinfo=dt_timezone.utc)
        self.old = datetime(2026, 5, 4, 12, tzinfo=dt_timezone.utc)
        self.owner = get_user_model().objects.create_user(username="retention-review-owner")

    def project(self, *, status="rejected", activity=None, revision=True):
        submission = Submission.objects.create(owner=self.owner, title="Private title", collaborators=[{"name": "Private person", "email": "private@example.invalid"}])
        stamp = activity or self.old
        Submission.objects.filter(pk=submission.pk).update(created_at=stamp, updated_at=stamp)
        if revision:
            record = SubmissionRevision.objects.create(submission=submission, number=1, status=status, snapshot=submission.snapshot())
            SubmissionRevision.objects.filter(pk=record.pk).update(created_at=stamp, reviewed_at=stamp if status != "pending" else None)
        return submission

    def test_calendar_boundary_and_end_of_month(self):
        self.assertEqual(six_months_before(self.now), self.old)
        august = datetime(2026, 8, 31, tzinfo=dt_timezone.utc)
        self.assertEqual(six_months_before(august).date().isoformat(), "2026-02-28")
        due = self.project()
        self.project(activity=self.old + timedelta(seconds=1))
        self.assertEqual([r["submission_id"] for r in retention_candidates(now=self.now)], [str(due.pk)])

    def test_recent_moderation_and_ever_approved_and_drafts_are_protected(self):
        due = self.project(status="pending")
        recent = self.project()
        recent.revisions.update(reviewed_at=self.now - timedelta(days=2))
        self.project(status="approved")  # Protected even if subsequently unpublished.
        self.project(revision=False)
        rows = list(retention_candidates(now=self.now))
        self.assertEqual([r["submission_id"] for r in rows], [str(due.pk)])
        self.assertEqual(rows[0]["pending_revisions"], 1)
        self.assertEqual(list(retention_candidates(now=self.now, static_public_ids=[due.public_id])), [])

    def test_report_is_read_only_and_does_not_print_contact_details(self):
        project = self.project()
        output = StringIO()
        with patch("submissions.management.commands.review_submission_retention.timezone.now", return_value=self.now):
            call_command("review_submission_retention", "--json", stdout=output)
        data = json.loads(output.getvalue())
        self.assertTrue(data["read_only"])
        self.assertEqual(data["candidates"][0]["submission_id"], str(project.pk))
        self.assertNotIn("Private", output.getvalue())
        self.assertNotIn("private@example.invalid", output.getvalue())
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(SubmissionRevision.objects.count(), 1)
