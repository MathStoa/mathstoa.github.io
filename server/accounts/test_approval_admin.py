"""Beta account decisions require an authorised moderator with current MFA."""
from allauth.account.internal.flows.login import record_authentication
from allauth.account.models import EmailAddress
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .models import AccountApproval


class AccountApprovalAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.User = get_user_model()
        self.moderator = self.User.objects.create_user(username="beta-reviewer", is_staff=True)
        self.moderator.user_permissions.add(Permission.objects.get(codename="change_accountapproval"))
        self.author = self.User.objects.create_user(username="beta-applicant")
        EmailAddress.objects.create(user=self.author, email="verified@example.invalid", verified=True, primary=True)
        self.application = AccountApproval.objects.create(user=self.author)
        self.list_url = reverse("admin:accounts_accountapproval_changelist")
        self.detail_url = reverse("admin:accounts_accountapproval_change", args=[self.application.pk])

    def sign_in(self, user, *, mfa=True):
        self.client.force_login(user)
        if user.is_staff and mfa:
            factor = TOTP.activate(user, generate_totp_secret()).instance
            request = RequestFactory().get("/")
            request.session = self.client.session
            record_authentication(request, user, "mfa", id=factor.pk, type="totp")
            request.session.save()

    def action(self, name):
        return self.client.post(self.list_url, {"action": name, "_selected_action": self.application.pk})

    def test_moderator_can_review_verified_identity_and_approve(self):
        EmailAddress.objects.create(user=self.author, email="unverified@example.invalid", verified=False)
        SocialAccount.objects.create(user=self.author, provider="github", uid="123456")
        self.sign_in(self.moderator)
        response = self.client.get(self.list_url)
        self.assertContains(response, "verified@example.invalid")
        self.assertNotContains(response, "unverified@example.invalid")
        self.assertContains(response, "GitHub")
        self.assertContains(response, "123456")
        self.assertContains(response, "Account applications (1)")
        self.assertIn("no-store", response["Cache-Control"])
        response = self.action("approve_selected")
        self.assertEqual(response.status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "approved")
        self.assertEqual(self.application.reviewed_by, self.moderator)
        self.assertIsNotNone(self.application.reviewed_at)

    def test_rejection_records_moderator_and_cannot_be_overridden_by_form(self):
        self.application.review_note = "Please contact the beta organisers."
        self.application.save(update_fields=["review_note"])
        self.sign_in(self.moderator)
        self.assertEqual(self.action("reject_selected").status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "rejected")
        self.assertEqual(self.application.reviewed_by, self.moderator)
        response = self.client.get(self.detail_url)
        self.assertNotContains(response, 'name="status"')
        self.assertNotContains(response, 'name="review_note"')
        self.client.post(self.detail_url, {"status": "approved", "review_note": "Altered", "_save": "Save"})
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "rejected")
        self.assertEqual(self.application.review_note, "Please contact the beta organisers.")

    def test_unverified_email_application_cannot_be_approved(self):
        EmailAddress.objects.filter(user=self.author).update(verified=False)
        self.sign_in(self.moderator)
        self.assertEqual(self.action("approve_selected").status_code, 302)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "pending")
        self.assertIsNone(self.application.reviewed_at)

    def test_view_permission_does_not_allow_decisions(self):
        viewer = self.User.objects.create_user(username="beta-viewer", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename="view_accountapproval"))
        self.sign_in(viewer)
        response = self.client.get(self.list_url)
        self.assertContains(response, "verified@example.invalid")
        self.assertNotContains(response, "Account applications (1)")
        self.assertNotContains(response, 'value="approve_selected"')
        self.action("approve_selected")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "pending")

    def test_queue_and_decisions_require_staff_permission_and_mfa(self):
        ordinary = self.User.objects.create_user(username="ordinary-beta-reviewer")
        ordinary.user_permissions.add(Permission.objects.get(codename="change_accountapproval"))
        staff = self.User.objects.create_user(username="staff-without-beta-permission", is_staff=True)
        for user, mfa in ((ordinary, True), (staff, True), (self.moderator, False)):
            with self.subTest(user=user.username):
                self.client.logout()
                self.sign_in(user, mfa=mfa)
                response = self.client.get(self.list_url)
                self.assertEqual(response.status_code, 403)
                self.assertNotContains(response, "verified@example.invalid", status_code=403)
                self.assertEqual(self.action("approve_selected").status_code, 403)
                self.application.refresh_from_db()
                self.assertEqual(self.application.status, "pending")

    @override_settings(ACCOUNT_APPROVAL_REQUIRED=True)
    def test_public_sign_in_and_signup_explain_beta_approval(self):
        response = self.client.get(reverse("account_login"))
        self.assertContains(response, "new accounts need moderator approval")
        self.assertNotContains(response, "Account applications")
        response = self.client.get(reverse("account_signup"))
        self.assertContains(response, "a moderator must approve your new account")
