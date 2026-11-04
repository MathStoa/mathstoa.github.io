"""Beta approval exports are scoped and do not extend applicant retention."""
import json
from datetime import timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import AccountApproval
from .privacy_services import account_export
from .retention import purge_retained_data


User = get_user_model()


class AccountApprovalPrivacyTests(TestCase):
    def test_export_includes_own_decision_without_reviewer_or_other_applicant_details(self):
        owner = User.objects.create_user(username="beta-export-owner")
        reviewer = User.objects.create_user(username="private-reviewer", email="private-reviewer@example.org", is_staff=True)
        reviewed_at = timezone.now()
        application = AccountApproval.objects.create(
            user=owner, status="approved", review_note="Welcome to the beta.",
            reviewed_by=reviewer, reviewed_at=reviewed_at,
        )
        other = User.objects.create_user(username="another-private-applicant")
        AccountApproval.objects.create(user=other, review_note="Another applicant's confidential note")

        data = account_export(owner)

        self.assertEqual(data["account_approval"], {
            "status": "approved", "review_note": application.review_note,
            "created_at": application.created_at.isoformat(),
            "reviewed_at": reviewed_at.isoformat(),
        })
        self.assertNotIn("reviewed_by", data["account_approval"])
        for private in (reviewer.username, reviewer.email, other.username, "Another applicant's confidential note"):
            self.assertNotIn(private, json.dumps(data))

    def test_reviewer_export_does_not_include_reviewed_applicants(self):
        reviewer = User.objects.create_user(username="beta-reviewer", is_staff=True)
        owner = User.objects.create_user(username="private-beta-applicant", email="private-applicant@example.org")
        AccountApproval.objects.create(
            user=owner, status="rejected", reviewed_by=reviewer,
            review_note="Private application decision", reviewed_at=timezone.now(),
        )

        data = account_export(reviewer)

        self.assertIsNone(data["account_approval"])
        for private in (owner.username, owner.email, "Private application decision"):
            self.assertNotIn(private, json.dumps(data))

    def test_unverified_application_expires_with_empty_account(self):
        now = timezone.now()
        owner = User.objects.create_user(username="unverified-beta", date_joined=now - timedelta(days=8))
        EmailAddress.objects.create(user=owner, email="unverified-beta@example.org", verified=False, primary=True)
        application = AccountApproval.objects.create(user=owner)

        counts = purge_retained_data(now=now)

        self.assertEqual(counts["unverified_accounts"], 1)
        self.assertFalse(User.objects.filter(pk=owner.pk).exists())
        self.assertFalse(AccountApproval.objects.filter(pk=application.pk).exists())

    def test_verified_old_applications_follow_inactive_account_retention(self):
        now = timezone.now()
        old = now - timedelta(days=400)
        for status in ("pending", "approved", "rejected"):
            owner = User.objects.create_user(username=f"inactive-beta-{status}", date_joined=old)
            EmailAddress.objects.create(user=owner, email=f"{status}@example.org", verified=True, primary=True)
            AccountApproval.objects.create(user=owner, status=status)
        current = User.objects.create_user(username="current-beta")
        AccountApproval.objects.create(user=current)

        counts = purge_retained_data(now=now)

        self.assertEqual(counts["inactive_accounts"], 3)
        self.assertEqual(list(AccountApproval.objects.values_list("user_id", flat=True)), [current.pk])

    def test_other_applicants_review_records_protect_reviewer_account(self):
        now = timezone.now()
        reviewer = User.objects.create_user(username="former-beta-reviewer", date_joined=now - timedelta(days=400))
        owner = User.objects.create_user(username="retained-beta-applicant")
        AccountApproval.objects.create(user=owner, status="approved", reviewed_by=reviewer, reviewed_at=now)

        purge_retained_data(now=now)

        self.assertTrue(User.objects.filter(pk=reviewer.pk).exists())
