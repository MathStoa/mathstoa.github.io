"""New account moderation applies after proof and across every sign-in path."""
import time
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import jwt
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AccountApproval


@override_settings(ACCOUNT_APPROVAL_REQUIRED=True)
class AccountApprovalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.User = get_user_model()
        self.moderator = self.User.objects.create_user(username="account-moderator", is_staff=True)
        self.permission = Permission.objects.get(
            codename="change_accountapproval", content_type__app_label="accounts",
        )
        self.moderator.user_permissions.add(self.permission)

    def signup_email(self, email="new-account@example.org"):
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_email_verification_code", return_value="123456"):
            response = self.client.post(reverse("account_signup"), {"email": email})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        return self.User.objects.get(email=email)

    def verify_email(self):
        return self.client.post(reverse("account_email_verification_sent"), {"code": "123456"})

    def login_email(self, email):
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="654321"):
            self.client.post(reverse("account_request_login_code"), {"email": email})
        return self.client.post(reverse("account_confirm_login_code"), {"code": "654321"})

    def oauth(self, provider="github", uid="197", *, connect=False):
        start = self.client.post(f"/accounts/{provider}/login/", {"process": "connect" if connect else "login"})
        self.assertEqual(start.status_code, 302)
        state = parse_qs(urlsplit(start.url).query)["state"][0]
        profile = {"id": int(uid), "name": "Unused provider name", "email": "unused@example.org"}
        token = {"access_token": "do-not-retain"}
        if provider == "orcid":
            uid = "0000-0002-1825-0097"
            profile = {"orcid-identifier": {"path": uid}}
            token["orcid"] = uid
        remote_response = Mock(status_code=200)
        remote_response.json.return_value = profile
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value = remote_response
        with patch("allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token", return_value=token), patch("allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_requests_session", return_value=session):
            return self.client.get(f"/accounts/{provider}/login/callback/", {"code": "test-code", "state": state})

    def institution_oauth(self):
        configuration = {
            "issuer": "https://university.example",
            "authorization_endpoint": "https://university.example/authorize",
            "token_endpoint": "https://university.example/token",
            "jwks_uri": "https://university.example/keys",
        }
        remote_response = Mock(status_code=200)
        remote_response.json.return_value = configuration
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value = remote_response
        with patch("allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_requests_session", return_value=session):
            start = self.client.post("/accounts/oidc/institution/login/")
            self.assertEqual(start.status_code, 302)
            state = parse_qs(urlsplit(start.url).query)["state"][0]
            claims = {
                "iss": configuration["issuer"], "sub": "stable-university-id",
                "aud": "test-university", "exp": int(time.time()) + 120,
                "iat": int(time.time()), "email": "unused@university.example",
            }
            token = jwt.encode(claims, "test-identity-server-signing-key-is-long", algorithm="HS256")
            with patch("allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token", return_value={"access_token": "temporary", "id_token": token}):
                return self.client.get("/accounts/oidc/institution/login/callback/", {"code": "test-code", "state": state})

    def test_email_signup_verifies_ownership_before_approval_blocks_login(self):
        user = self.signup_email()
        approval = AccountApproval.objects.get(user=user)
        self.assertEqual(approval.status, "pending")
        self.assertFalse(EmailAddress.objects.get(user=user).verified)
        with self.assertRaises(ValidationError):
            approval.review(self.moderator, approve=True)
        response = self.verify_email()
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "account/approval_required.html")
        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.get(reverse("submissions:create")).status_code, 302)

    def test_approved_email_account_can_sign_in_without_a_second_review(self):
        user = self.signup_email()
        self.verify_email()
        approval = AccountApproval.objects.get(user=user)
        approval.review(self.moderator, approve=True)
        approval.refresh_from_db()
        self.assertEqual(approval.status, "approved")
        self.assertEqual(approval.reviewed_by_id, self.moderator.pk)
        self.assertIsNotNone(approval.reviewed_at)
        self.login_email(user.email)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.client.logout()
        self.login_email(user.email)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertEqual(AccountApproval.objects.filter(user=user).count(), 1)

    def test_pending_and_rejected_email_accounts_cannot_complete_login(self):
        user = self.signup_email()
        self.verify_email()
        approval = AccountApproval.objects.get(user=user)
        for status in ("pending", "rejected"):
            with self.subTest(status=status):
                if status == "rejected":
                    approval.review(self.moderator, approve=False, note="Registration is not approved.")
                cache.clear()
                response = self.login_email(user.email)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("_auth_user_id", self.client.session)
                self.assertEqual(AccountApproval.objects.filter(user=user).count(), 1)

    def test_each_social_provider_queues_new_accounts_without_requiring_email(self):
        for provider in ("github", "gitlab", "orcid"):
            with self.subTest(provider=provider):
                self.client.logout()
                response = self.oauth(provider)
                self.assertEqual(response.status_code, 403)
                self.assertTemplateUsed(response, "account/approval_required.html")
                social = SocialAccount.objects.get(provider=provider)
                approval = AccountApproval.objects.get(user=social.user)
                self.assertEqual(approval.status, "pending")
                self.assertFalse(EmailAddress.objects.filter(user=social.user).exists())
                self.assertEqual(social.extra_data, {})
                self.assertNotIn("_auth_user_id", self.client.session)
                approval.review(self.moderator, approve=True)
                response = self.oauth(provider)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(int(self.client.session["_auth_user_id"]), social.user_id)
                self.assertEqual(AccountApproval.objects.filter(user=social.user).count(), 1)

    def test_repeat_social_login_cannot_create_a_second_pending_account(self):
        self.oauth()
        pending = AccountApproval.objects.get()
        response = self.oauth()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AccountApproval.objects.count(), 1)
        self.assertEqual(SocialAccount.objects.count(), 1)
        pending.review(self.moderator, approve=False, note="Registration is not approved.")
        response = self.oauth()
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(AccountApproval.objects.count(), 1)

    def test_institution_signin_requires_the_same_approval_as_other_providers(self):
        response = self.institution_oauth()
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "account/approval_required.html")
        social = SocialAccount.objects.get(provider="institution", uid="stable-university-id")
        approval = AccountApproval.objects.get(user=social.user)
        self.assertEqual(approval.status, "pending")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(social.extra_data, {})
        self.assertFalse(EmailAddress.objects.filter(user=social.user).exists())
        approval.review(self.moderator, approve=True)
        response = self.institution_oauth()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), social.user_id)
        self.assertEqual(AccountApproval.objects.filter(user=social.user).count(), 1)

    def test_failed_approval_creation_rolls_back_email_signup(self):
        with patch("accounts.approval.AccountApproval.objects.get_or_create", side_effect=DatabaseError("Approval storage is unavailable")):
            with self.assertRaises(DatabaseError):
                self.client.post(reverse("account_signup"), {"email": "new-account@example.org"})
        self.assertEqual(list(self.User.objects.values_list("pk", flat=True)), [self.moderator.pk])
        self.assertFalse(EmailAddress.objects.exists())
        self.assertFalse(AccountApproval.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)
        cache.clear()
        user = self.signup_email()
        self.assertEqual(AccountApproval.objects.get(user=user).status, "pending")

    def test_failed_approval_creation_rolls_back_social_signup(self):
        with patch("accounts.approval.AccountApproval.objects.get_or_create", side_effect=DatabaseError("Approval storage is unavailable")):
            with self.assertRaises(DatabaseError):
                self.oauth()
        self.assertEqual(list(self.User.objects.values_list("pk", flat=True)), [self.moderator.pk])
        self.assertFalse(SocialAccount.objects.exists())
        self.assertFalse(EmailAddress.objects.exists())
        self.assertFalse(AccountApproval.objects.exists())
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.oauth()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(AccountApproval.objects.get(user=SocialAccount.objects.get().user).status, "pending")

    def test_linking_a_provider_does_not_create_or_reset_an_admission_request(self):
        for approved in (False, True):
            with self.subTest(already_reviewed=approved):
                self.client.logout()
                user = self.User.objects.create_user(
                    username=f"admitted-{approved}", email=f"admitted-{int(approved)}@example.org",
                )
                EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
                approval = None
                if approved:
                    approval = AccountApproval.objects.create(user=user)
                    approval.review(self.moderator, approve=True)
                self.login_email(user.email)
                self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
                response = self.oauth(uid=str(198 + int(approved)), connect=True)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(SocialAccount.objects.get(uid=str(198 + int(approved))).user_id, user.pk)
                self.assertEqual(AccountApproval.objects.filter(user=user).count(), int(approved))
                if approval is not None:
                    approval.refresh_from_db()
                    self.assertEqual(approval.status, "approved")
                    self.assertEqual(approval.reviewed_by_id, self.moderator.pk)

    def test_existing_email_and_provider_accounts_keep_access(self):
        user = self.User.objects.create_user(username="existing-member", email="existing@example.org")
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
        SocialAccount.objects.create(user=user, provider="github", uid="197")
        self.login_email(user.email)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.client.logout()
        self.oauth()
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertFalse(AccountApproval.objects.filter(user=user).exists())

    @override_settings(ACCOUNT_APPROVAL_REQUIRED=False)
    def test_disabling_moderation_restores_automatic_access_for_new_accounts(self):
        email_user = self.signup_email()
        self.verify_email()
        self.assertEqual(int(self.client.session["_auth_user_id"]), email_user.pk)
        self.client.logout()
        response = self.oauth()
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertFalse(AccountApproval.objects.exists())

    def test_disabling_moderation_does_not_release_existing_pending_accounts(self):
        user = self.signup_email()
        self.verify_email()
        with override_settings(ACCOUNT_APPROVAL_REQUIRED=False):
            response = self.login_email(user.email)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_existing_session_is_revoked_before_a_protected_action(self):
        user = self.signup_email()
        self.verify_email()
        approval = AccountApproval.objects.get(user=user)
        approval.review(self.moderator, approve=True)
        self.login_email(user.email)
        self.assertIn("_auth_user_id", self.client.session)
        for status in ("pending", "rejected"):
            with self.subTest(status=status):
                AccountApproval.objects.filter(pk=approval.pk).update(status=status)
                self.client.force_login(user)
                with override_settings(ACCOUNT_APPROVAL_REQUIRED=False):
                    response = self.client.post(reverse("submissions:create"), {"title": "Must not be created"})
                self.assertEqual(response.status_code, 302)
                self.assertNotIn("_auth_user_id", self.client.session)
                self.assertFalse(user.owned_submissions.exists())

    def test_review_change_while_waiting_for_a_login_code_blocks_completion(self):
        user = self.signup_email()
        self.verify_email()
        approval = AccountApproval.objects.get(user=user)
        approval.review(self.moderator, approve=True)
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="654321"):
            self.client.post(reverse("account_request_login_code"), {"email": user.email})
        AccountApproval.objects.filter(pk=approval.pk).update(status="rejected")
        response = self.client.post(reverse("account_confirm_login_code"), {"code": "654321"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_review_requires_active_staff_permission_and_cannot_review_self(self):
        user = self.signup_email()
        self.verify_email()
        approval = AccountApproval.objects.get(user=user)
        ordinary = self.User.objects.create_user(username="ordinary-with-permission")
        ordinary.user_permissions.add(self.permission)
        no_permission = self.User.objects.create_user(username="staff-without-permission", is_staff=True)
        inactive = self.User.objects.create_user(username="inactive-moderator", is_staff=True, is_active=False)
        inactive.user_permissions.add(self.permission)
        for reviewer in (ordinary, no_permission, inactive):
            with self.subTest(reviewer=reviewer.username):
                with self.assertRaises(ValidationError):
                    approval.review(reviewer, approve=True)
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        user.user_permissions.add(self.permission)
        with self.assertRaises(ValidationError):
            approval.review(user, approve=True)
        approval.refresh_from_db()
        self.assertEqual(approval.status, "pending")
        approval.review(self.moderator, approve=True)
        with self.assertRaises(ValidationError):
            approval.review(self.moderator, approve=True)

    def test_unverified_attempts_cannot_start_a_signed_in_session(self):
        self.signup_email()
        response = self.client.post(reverse("account_email_verification_sent"), {"code": "000000"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotEqual(response.status_code, 403)
        self.assertFalse(EmailAddress.objects.get().verified)
