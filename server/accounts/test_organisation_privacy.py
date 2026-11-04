"""Organisation exports stay private and application records survive account cleanup."""
import json
from datetime import timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import OrganisationAccount
from .privacy_services import account_export
from .retention import purge_retained_data


User = get_user_model()


class OrganisationPrivacyTests(TestCase):
    def organisation(self, user, **changes):
        return OrganisationAccount.objects.create(
            user=user,
            **{
                "name": "Example Research Group",
                "email": user.email,
                "website": "https://example.org/research/",
                "description": "A mathematical research group.",
                "representative_name": "Alex Example",
                "authority": "Authorised to administer this organisation's account.",
                **changes,
            },
        )

    def test_export_includes_own_application_and_decision_without_reviewer_identity(self):
        owner = User.objects.create_user(username="organisation-export", email="office@example.org")
        reviewer = User.objects.create_user(username="private-moderator", email="moderator-private@example.org", is_staff=True)
        reviewed_at = timezone.now()
        application = self.organisation(
            owner, status="approved", reviewed_by=reviewer, reviewed_at=reviewed_at,
            review_note="Authority confirmed against the organisation website.",
        )
        other = User.objects.create_user(username="other-organisation", email="other-private@example.org")
        self.organisation(other, name="Other private organisation", authority="Other confidential authority evidence")

        data = account_export(owner)
        self.assertEqual(data["organisation_account"], {
            "name": application.name, "email": application.email,
            "website": application.website, "description": application.description,
            "representative_name": application.representative_name,
            "authority": application.authority, "status": "approved",
            "review_note": application.review_note,
            "created_at": application.created_at.isoformat(),
            "reviewed_at": reviewed_at.isoformat(),
        })
        text = json.dumps(data)
        for private in (reviewer.username, reviewer.email, other.email, "Other private organisation", "Other confidential authority evidence"):
            self.assertNotIn(private, text)
        self.assertNotIn("reviewed_by", data["organisation_account"])

    def test_staff_export_does_not_include_applications_they_reviewed(self):
        reviewer = User.objects.create_user(username="organisation-reviewer", is_staff=True, is_superuser=True)
        owner = User.objects.create_user(username="private-applicant", email="applicant-private@example.org")
        self.organisation(owner, reviewed_by=reviewer, reviewed_at=timezone.now(), status="rejected", review_note="Private decision explanation")

        data = account_export(reviewer)
        self.assertIsNone(data["organisation_account"])
        for private in (owner.email, "Example Research Group", "Private decision explanation"):
            self.assertNotIn(private, json.dumps(data))

    def test_cleanup_preserves_pending_rejected_and_approved_application_accounts(self):
        now = timezone.now()
        old = now - timedelta(days=400)
        reviewer = User.objects.create_user(username="former-organisation-reviewer", date_joined=old)
        accounts = []
        for status in ("pending", "rejected", "approved"):
            user = User.objects.create_user(username=f"old-organisation-{status}", email=f"{status}@example.org", date_joined=old, is_active=status == "approved")
            EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=False)
            self.organisation(
                user, status=status,
                reviewed_by=reviewer if status != "pending" else None,
                reviewed_at=old if status != "pending" else None,
                review_note="Recorded decision" if status != "pending" else "",
            )
            accounts.append(user)
        empty = User.objects.create_user(username="old-empty-signup", date_joined=old)

        counts = purge_retained_data(now=now)

        self.assertEqual(counts["unverified_accounts"], 1)
        self.assertEqual(counts["inactive_accounts"], 0)
        self.assertFalse(User.objects.filter(pk=empty.pk).exists())
        for user in [*accounts, reviewer]:
            self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertEqual(OrganisationAccount.objects.count(), 3)
