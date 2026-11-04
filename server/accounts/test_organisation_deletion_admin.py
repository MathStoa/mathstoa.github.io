"""The moderator deletion page requires a deliberate, authorised confirmation."""
from unittest.mock import patch

from allauth.account.internal.flows.login import record_authentication
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.contrib import admin
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from submissions.models import Submission
from .models import OrganisationAccount


class OrganisationDeletionAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.moderator = get_user_model().objects.create_user(username="deletion-moderator", is_staff=True)
        self.moderator.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="accounts", codename__in=("change_organisationaccount", "delete_organisationaccount"),
        ))
        self.user = get_user_model().objects.create_user(username="delete-organisation", email="delete-group@example.org", is_active=False)
        EmailAddress.objects.create(user=self.user, email=self.user.email, primary=True, verified=False)
        self.organisation = OrganisationAccount.objects.create(
            user=self.user, name="Deletion Research Group", email=self.user.email,
            website="https://example.org", description="Research group", representative_name="Representative",
            authority="Authorised administrator",
        )
        self.url = reverse("admin:accounts_organisationaccount_delete", args=[self.organisation.pk])
        self.change_url = reverse("admin:accounts_organisationaccount_change", args=[self.organisation.pk])
        self.list_url = reverse("admin:accounts_organisationaccount_changelist")
        self.model_admin = admin.site._registry[OrganisationAccount]

    def sign_in(self, *, client=None, user=None, mfa=True):
        client = client or self.client
        user = user or self.moderator
        client.force_login(user)
        if mfa and user.is_staff:
            factor = Authenticator.objects.filter(user=user, type=Authenticator.Type.TOTP).first()
            if factor is None:
                factor = TOTP.activate(user, generate_totp_secret()).instance
            request = RequestFactory().get("/")
            request.session = client.session
            record_authentication(request, user, "mfa", id=factor.pk, type="totp")
            request.session.save()
        return client

    def assert_preserved(self):
        self.assertTrue(OrganisationAccount.objects.filter(pk=self.organisation.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
        self.assertTrue(EmailAddress.objects.filter(user=self.user).exists())
        self.assertFalse(LogEntry.objects.filter(action_flag=DELETION).exists())

    def test_change_page_has_individual_delete_link_and_get_only_confirms(self):
        self.sign_in()
        self.assertContains(self.client.get(self.change_url), f'href="{self.url}"')
        response = self.client.get(self.url)
        self.assertContains(response, self.organisation.name)
        self.assertContains(response, self.organisation.email)
        self.assertContains(response, "both the organisation account and its sign-in account")
        self.assertContains(response, 'name="confirm_name"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, f'href="{self.change_url}">Cancel</a>')
        self.assert_preserved()
        self.client.get(self.change_url)
        self.assert_preserved()

    def test_correct_confirmation_deletes_both_accounts_and_keeps_id_only_audit(self):
        self.sign_in()
        organisation_id, user_id = self.organisation.pk, self.user.pk
        response = self.client.post(self.url, {"confirm_name": self.organisation.name})
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.assertFalse(OrganisationAccount.objects.filter(pk=organisation_id).exists())
        self.assertFalse(get_user_model().objects.filter(pk=user_id).exists())
        self.assertFalse(EmailAddress.objects.filter(user_id=user_id).exists())
        log = LogEntry.objects.get(action_flag=DELETION)
        self.assertEqual(log.user_id, self.moderator.pk)
        self.assertEqual(log.object_id, str(organisation_id))
        self.assertEqual(log.object_repr, f"Organisation account #{organisation_id}")
        self.assertNotIn(self.organisation.name, log.object_repr + log.change_message)
        self.assertNotIn(self.organisation.email, log.object_repr + log.change_message)

    def test_missing_inexact_and_standard_admin_confirmation_cannot_delete(self):
        self.sign_in()
        for data in ({}, {"post": "yes"}, {"confirm_name": self.organisation.name.lower()}, {"confirm_name": self.organisation.name + " "}):
            with self.subTest(data=data):
                response = self.client.post(self.url, data)
                self.assertContains(response, "Type the organisation name exactly", status_code=400)
                self.assert_preserved()

    def test_current_name_is_required_after_confirmation_page_was_opened(self):
        self.sign_in()
        self.client.get(self.url)
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(name="Renamed Research Group")
        response = self.client.post(self.url, {"confirm_name": self.organisation.name})
        self.assertContains(response, "Renamed Research Group", status_code=400)
        self.assert_preserved()

    def test_blocked_account_has_no_delete_form_and_post_keeps_records(self):
        self.sign_in()
        Submission.objects.create(owner=self.user, title="Retained mathematical project")
        response = self.client.get(self.url)
        self.assertContains(response, "This organisation account cannot be deleted")
        self.assertNotContains(response, 'name="confirm_name"')
        self.assertNotContains(response, 'value="Delete organisation and sign-in account"')
        response = self.client.post(self.url, {"confirm_name": self.organisation.name})
        self.assertContains(response, "cannot be deleted", status_code=400)
        self.assert_preserved()
        self.assertEqual(Submission.objects.filter(owner=self.user).count(), 1)

    def test_new_project_after_get_is_checked_again_before_delete(self):
        self.sign_in()
        self.client.get(self.url)
        Submission.objects.create(owner=self.user, title="New linked project")
        response = self.client.post(self.url, {"confirm_name": self.organisation.name})
        self.assertContains(response, "cannot be deleted", status_code=400)
        self.assert_preserved()

    def test_service_validation_error_rolls_back_audit_and_redisplays_confirmation(self):
        self.sign_in()
        with patch("accounts.organisation_deletion.delete_organisation", side_effect=ValidationError("The account has acquired a protected responsibility.")):
            response = self.client.post(self.url, {"confirm_name": self.organisation.name})
        self.assertContains(response, "protected responsibility", status_code=400)
        self.assertContains(response, self.organisation.name, status_code=400)
        self.assert_preserved()

    def test_permission_and_staff_checks_apply_to_direct_posts(self):
        for name, staff, permissions in (
            ("read-only", True, []),
            ("change-only", True, ["change_organisationaccount"]),
            ("delete-only", True, ["delete_organisationaccount"]),
            ("not-staff", False, ["change_organisationaccount", "delete_organisationaccount"]),
        ):
            with self.subTest(role=name):
                user = get_user_model().objects.create_user(username="delete-" + name, is_staff=staff)
                user.user_permissions.add(*Permission.objects.filter(content_type__app_label="accounts", codename__in=permissions))
                self.sign_in(user=user)
                self.assertEqual(self.client.post(self.url, {"confirm_name": self.organisation.name}).status_code, 403)
                self.assert_preserved()
                self.client.logout()

    def test_inactive_or_anonymous_moderator_cannot_delete(self):
        self.assertEqual(self.client.post(self.url, {"confirm_name": self.organisation.name}).status_code, 302)
        self.moderator.is_active = False
        self.moderator.save(update_fields=["is_active"])
        self.sign_in(mfa=False)
        self.assertEqual(self.client.post(self.url, {"confirm_name": self.organisation.name}).status_code, 302)
        self.assert_preserved()

    def test_staff_mfa_is_required_for_delete_confirmation_and_post(self):
        self.sign_in(mfa=False)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"confirm_name": self.organisation.name}).status_code, 403)
        self.assert_preserved()

    def test_delete_post_requires_csrf_even_with_mfa_and_exact_name(self):
        client = self.sign_in(client=Client(enforce_csrf_checks=True))
        self.assertEqual(client.get(self.url).status_code, 200)
        self.assertEqual(client.post(self.url, {"confirm_name": self.organisation.name}).status_code, 403)
        self.assert_preserved()
        response = client.post(self.url, {
            "confirm_name": self.organisation.name,
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(OrganisationAccount.objects.filter(pk=self.organisation.pk).exists())

    def test_self_staff_and_superuser_targets_have_no_delete_permission(self):
        request = RequestFactory().get(self.url)
        request.user = self.moderator
        for field in ("is_staff", "is_superuser"):
            with self.subTest(field=field):
                setattr(self.organisation.user, field, True)
                self.assertFalse(self.model_admin.has_delete_permission(request, self.organisation))
                setattr(self.organisation.user, field, False)
        self.organisation.user = self.moderator
        self.assertFalse(self.model_admin.has_delete_permission(request, self.organisation))

    def test_bulk_delete_is_removed_and_forged_action_does_nothing(self):
        self.sign_in()
        request = RequestFactory().get(self.list_url)
        request.user = self.moderator
        self.assertNotIn("delete_selected", self.model_admin.get_actions(request))
        self.assertNotContains(self.client.get(self.list_url), 'value="delete_selected"')
        self.client.post(self.list_url, {"action": "delete_selected", "_selected_action": self.organisation.pk, "post": "yes"})
        self.assert_preserved()
        with self.assertRaises(PermissionDenied):
            self.model_admin.delete_queryset(request, OrganisationAccount.objects.all())

    def test_standard_model_deletion_hook_uses_protected_service(self):
        request = RequestFactory().post(self.url)
        request.user = self.moderator
        with patch("accounts.organisation_deletion.delete_organisation") as delete:
            self.model_admin.delete_model(request, self.organisation)
        delete.assert_called_once_with(self.organisation, moderator=self.moderator)
