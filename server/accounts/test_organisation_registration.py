"""Registration cannot activate an organisation; only recorded moderation can."""
from io import StringIO
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from .models import OrganisationAccount
from .organisation_notifications import process_organisation_notifications


class OrganisationRegistrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.data = {
            "name": "Example Mathematics Institute", "website": "https://example.org",
            "description": "An independent mathematical research institute.",
            "email": "INSTITUTE@example.org", "representative_name": "Example Director",
            "authority": "Director, listed on the institute website.",
        }
        self.moderator = get_user_model().objects.create_user(username="organisation-moderator", is_staff=True)
        self.moderator.user_permissions.add(Permission.objects.get(codename="change_organisationaccount"))

    def register(self, **extra):
        return self.client.post(reverse("organisation_register"), {**self.data, **extra})

    def application(self):
        self.register()
        return OrganisationAccount.objects.get()

    def test_registration_is_private_inactive_and_cannot_forge_approval(self):
        response = self.register(status="approved", is_active="on", is_staff="on", reviewed_by=self.moderator.pk)
        self.assertRedirects(response, reverse("organisation_registration_received"))
        application = OrganisationAccount.objects.get()
        self.assertEqual(application.status, "pending")
        self.assertIsNone(application.reviewed_by)
        self.assertEqual(application.email, "institute@example.org")
        self.assertFalse(application.user.is_active)
        self.assertFalse(application.user.is_staff)
        self.assertFalse(application.user.has_usable_password())
        self.assertFalse(EmailAddress.objects.get(user=application.user).verified)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotContains(self.client.get("/data/projects.json"), application.email)

    def test_duplicate_and_personal_mailboxes_get_same_receipt_without_changes(self):
        first = self.register()
        second = self.register(email="institute@EXAMPLE.ORG", name="Replacement name")
        self.assertEqual(first.url, second.url)
        application = OrganisationAccount.objects.get()
        self.assertEqual(application.name, self.data["name"])
        personal = get_user_model().objects.create_user(username="personal", email="personal@example.org")
        response = self.register(email="PERSONAL@example.org")
        self.assertEqual(response.url, first.url)
        personal.refresh_from_db()
        self.assertTrue(personal.is_active)
        self.assertEqual(OrganisationAccount.objects.count(), 1)

    def test_required_details_and_valid_website_are_enforced(self):
        response = self.register(authority="", website="javascript:alert(1)")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OrganisationAccount.objects.exists())
        self.assertIn("authority", response.context["form"].errors)
        self.assertIn("website", response.context["form"].errors)

    def test_registration_requires_csrf_and_obeys_closed_registration(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("organisation_register"), self.data).status_code, 403)
        with override_settings(PUBLIC_REGISTRATION_ENABLED=False):
            self.assertEqual(self.register().status_code, 403)
        with override_settings(ORGANISATION_LOGIN_ENABLED=False):
            self.assertEqual(self.register().status_code, 403)
        self.assertFalse(OrganisationAccount.objects.exists())

    @override_settings(ACCOUNT_RATE_LIMITS={"organisation_register": "1/h/key"})
    def test_registration_limit_applies_across_sessions_and_ignores_forwarded_ip(self):
        self.assertEqual(self.register().status_code, 302)
        response = Client().post(reverse("organisation_register"), {**self.data, "email": "second@example.org"}, HTTP_X_FORWARDED_FOR="203.0.113.77")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(OrganisationAccount.objects.count(), 1)

    def test_only_authorised_moderator_can_activate_and_record_decision(self):
        application = self.application()
        nonstaff = get_user_model().objects.create_user(username="nonstaff")
        nonstaff.user_permissions.add(Permission.objects.get(codename="change_organisationaccount"))
        staff = get_user_model().objects.create_user(username="staff-only", is_staff=True)
        for reviewer in (application.user, nonstaff, staff):
            with self.assertRaises(ValidationError):
                application.review(reviewer, approve=True)
        application.refresh_from_db()
        self.assertFalse(application.user.is_active)
        application.review(self.moderator, approve=True, note="Representation confirmed.")
        self.assertEqual(application.status, "approved")
        self.assertTrue(application.user.is_active)
        self.assertEqual(application.reviewed_by, self.moderator)
        self.assertIsNotNone(application.reviewed_at)
        self.assertEqual(application.notification_status, "queued")
        # Approval establishes eligibility, not proof of mailbox control.
        self.assertFalse(EmailAddress.objects.get(user=application.user).verified)
        self.assertEqual(len(mail.outbox), 0)
        with self.assertRaises(ValidationError):
            application.review(self.moderator, approve=False, note="Stale decision")

    def test_rejection_needs_reason_and_preserves_inactive_account(self):
        application = self.application()
        with self.assertRaises(ValidationError):
            application.review(self.moderator, approve=False)
        application.review(self.moderator, approve=False, note="Unable to verify representation.")
        self.assertEqual(application.status, "rejected")
        self.assertFalse(application.user.is_active)
        counts = process_organisation_notifications()
        self.assertEqual(counts["sent"], 1)
        self.assertIn("Unable to verify representation.", mail.outbox[0].body)
        self.assertIn("remains inactive", mail.outbox[0].body)

    def test_admin_approval_action_requires_staff_mfa_and_activates_account(self):
        from allauth.account.internal.flows.login import record_authentication
        from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret

        application = self.application()
        self.client.force_login(self.moderator)
        url = reverse("admin:accounts_organisationaccount_changelist")
        action = {"action": "approve_selected", "_selected_action": application.pk}
        self.assertEqual(self.client.post(url, action).status_code, 403)
        application.refresh_from_db()
        self.assertEqual(application.status, "pending")
        factor = TOTP.activate(self.moderator, generate_totp_secret()).instance
        request = RequestFactory().get("/")
        request.session = self.client.session
        record_authentication(request, self.moderator, "mfa", id=factor.pk, type="totp")
        request.session.save()
        self.assertEqual(self.client.post(url, action).status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, "approved")
        self.assertTrue(application.user.is_active)

    def test_email_collision_prevents_approval_without_changing_either_account(self):
        application = self.application()
        other = get_user_model().objects.create_user(username="conflict", email=application.email)
        with self.assertRaises(ValidationError):
            application.review(self.moderator, approve=True)
        application.refresh_from_db()
        self.assertEqual(application.status, "pending")
        self.assertFalse(application.user.is_active)
        self.assertTrue(other.is_active)

    @override_settings(ACCOUNT_APPROVAL_REQUIRED=True)
    def test_organisation_approval_alone_allows_its_own_login_during_beta(self):
        application = self.application()
        application.review(self.moderator, approve=True)
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="123456"):
            self.client.post(reverse("organisation_login"), {"email": application.email})
        self.client.post(reverse("account_confirm_login_code"), {"code": "123456"})
        self.assertEqual(int(self.client.session["_auth_user_id"]), application.user_id)
        self.assertContains(self.client.get(reverse("socialaccount_connections")), application.name)

    def test_delivery_budget_leaves_unattempted_decisions_queued(self):
        application = self.application()
        application.review(self.moderator, approve=True)
        with patch("accounts.organisation_notifications.monotonic", side_effect=[0, 91]):
            counts = process_organisation_notifications()
        self.assertEqual(counts, {"sent": 0, "failed": 0, "remaining": 1})
        application.refresh_from_db()
        self.assertEqual(application.notification_status, "queued")
        self.assertIsNone(application.notification_attempted_at)

    def test_worker_sends_approval_once_and_keeps_failed_sends_visible(self):
        application = self.application()
        application.review(self.moderator, approve=True)
        call_command("process_notification_queue", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [application.email])
        self.assertIn(reverse("organisation_login"), mail.outbox[0].body)
        application.refresh_from_db()
        self.assertIsNotNone(application.decision_notified_at)
        self.assertEqual(process_organisation_notifications()["sent"], 0)
        self.register(email="another@example.org")
        second = OrganisationAccount.objects.get(email="another@example.org")
        second.review(self.moderator, approve=True)
        with patch("accounts.organisation_notifications.EmailMessage.send", side_effect=OSError("private error")):
            self.assertEqual(process_organisation_notifications()["failed"], 1)
        second.refresh_from_db()
        self.assertEqual(second.notification_status, "failed")
        self.assertIsNone(second.decision_notified_at)
        self.assertEqual(process_organisation_notifications()["sent"], 0)
