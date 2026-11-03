"""Exercise encryption and real allauth factors, including admin bypass attempts."""
import time
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from allauth.account.internal.flows.login import record_authentication
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, generate_totp_secret, hotp_value
from allauth.socialaccount.models import SocialAccount
from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.db import SessionStore as PlainSessionStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .mfa_adapter import MFAAdapter
from .security_crypto import decrypt, encrypt
from .sessions import SessionStore

User = get_user_model()


class EncryptedSessionTests(TestCase):
    def test_database_payload_encrypts_pending_secrets_and_remains_django_compatible(self):
        data = {"mfa.totp.secret": "pending-totp-secret", "login_code": "123456", "oauth_state": "private-oauth-state"}
        session = SessionStore()
        session.update(data)
        session.save()
        stored = Session.objects.get(session_key=session.session_key)
        self.assertNotIn("pending-totp-secret", stored.session_data)
        self.assertNotIn("123456", stored.session_data)
        self.assertNotIn("private-oauth-state", stored.session_data)
        self.assertEqual(SessionStore().decode(stored.session_data), data)
        self.assertEqual(SessionStore(session.session_key).load(), data)

    def test_tampered_and_legacy_plaintext_sessions_fail_closed(self):
        encrypted = SessionStore().encode({"_auth_user_id": "1"})
        for bad in (encrypted[:-10] + "corrupted!", PlainSessionStore().encode({"_auth_user_id": "1"})):
            with self.subTest(value_type=bad[:10]), self.assertLogs("django.security.SuspiciousSession", level="WARNING"):
                self.assertEqual(SessionStore().decode(bad), {})

    def test_purposes_and_master_keys_are_separated(self):
        token = encrypt(b"private-value", purpose="sessions")
        with self.assertRaises(InvalidToken):
            decrypt(token, purpose="mfa")
        with override_settings(SECRET_KEY="different-test-master-" + "x" * 64, SECRET_KEY_FALLBACKS=[]):
            with self.assertRaises(InvalidToken):
                decrypt(token, purpose="sessions")

    def test_previous_key_can_decrypt_but_new_writes_use_current_key(self):
        old_key = settings.SECRET_KEY
        data = {"pending": "test-authentication-code"}
        old_session = SessionStore().encode(data)
        old_mfa = MFAAdapter().encrypt("test-totp-secret")
        new_key = "new-test-master-" + "x" * 64
        with override_settings(SECRET_KEY=new_key, SECRET_KEY_FALLBACKS=[old_key]):
            self.assertEqual(SessionStore().decode(old_session), data)
            self.assertEqual(MFAAdapter().decrypt(old_mfa), "test-totp-secret")
            new_session = SessionStore().encode(data)
        with override_settings(SECRET_KEY=new_key, SECRET_KEY_FALLBACKS=[]):
            self.assertEqual(SessionStore().decode(new_session), data)
            with self.assertLogs("django.security.SuspiciousSession", level="WARNING"):
                self.assertEqual(SessionStore().decode(old_session), {})


class StaffMFATests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="staff", is_staff=True, is_superuser=True)
        self.client.force_login(self.user)

    def record(self, method, *, user=None, at=None, **extra):
        request = RequestFactory().get("/")
        request.session = self.client.session
        record_authentication(request, user or self.user, method, **extra)
        if at is not None:
            request.session["account_authentication_methods"][-1]["at"] = at
        request.session.save()

    def primary(self):
        secret = generate_totp_secret()
        auth = TOTP.activate(self.user, secret).instance
        return auth, secret

    def confirm(self, secret):
        code = format_hotp_value(hotp_value(secret, int(time.time()) // 30))
        return self.client.post(reverse("mfa_reauthenticate"), {"code": code, "next": reverse("admin:index")})

    def test_no_anonymous_or_password_only_admin_login(self):
        self.client.logout()
        self.user.set_password("this-long-local-password-does-not-enable-signin")
        self.user.save()
        self.assertIsNone(authenticate(username=self.user.username, password="this-long-local-password-does-not-enable-signin"))
        response = self.client.post(reverse("admin:login"), {"username": self.user.username, "password": "this-long-local-password-does-not-enable-signin"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("account_login")))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_without_factor_needs_fresh_bound_existing_signin_for_enrollment(self):
        url = reverse("mfa_activate_totp")
        self.assertEqual(self.client.get(url).status_code, 403)
        for method, at in (("password", time.time()), ("socialaccount", time.time() - 301), ("code", time.time() + 60)):
            self.record(method, at=at)
            self.assertEqual(self.client.get(url).status_code, 403)
        other = User.objects.create_user(username="other")
        self.record("socialaccount", user=other)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.record("socialaccount", provider="github", uid="staff-id")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(EmailAddress.objects.filter(user=self.user).exists())

    def test_real_enrollment_encrypts_secret_and_requires_explicit_mfa_before_admin(self):
        self.record("socialaccount", provider="orcid", uid="0000-0002-1825-0097")
        self.client.get(reverse("mfa_activate_totp"))
        secret = self.client.session["mfa.totp.secret"]
        raw = Session.objects.get(session_key=self.client.session.session_key).session_data
        self.assertNotIn(secret, raw)
        code = format_hotp_value(hotp_value(secret, int(time.time()) // 30))
        response = self.client.post(reverse("mfa_activate_totp"), {"code": code})
        self.assertEqual(response.status_code, 302)
        factor = Authenticator.objects.get(user=self.user, type=Authenticator.Type.TOTP)
        self.assertNotEqual(factor.data["secret"], secret)
        self.assertEqual(MFAAdapter().decrypt(factor.data["secret"]), secret)
        self.assertNotIn("mfa.totp.secret", self.client.session)
        self.assertRedirects(self.client.get(reverse("admin:index")), reverse("mfa_reauthenticate") + "?next=%2Fadmin%2F", fetch_redirect_response=False)
        self.assertEqual(self.confirm(secret).status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_live_device_without_session_proof_cannot_access_admin(self):
        self.primary()
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("mfa_reauthenticate")))

    def test_expired_future_wrong_user_and_deleted_mfa_proof_cannot_access_admin(self):
        auth, secret = self.primary()
        other = User.objects.create_user(username="other-staff", is_staff=True)
        for at, user in ((time.time() - 901, self.user), (time.time() + 60, self.user), (time.time(), other)):
            self.record("mfa", user=user, at=at, id=auth.pk, type="totp")
            self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        self.record("mfa", id=auth.pk, type="totp")
        auth.delete()
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 403)

    def test_record_for_another_live_device_cannot_access_admin(self):
        self.primary()
        other = User.objects.create_user(username="another-factor-owner")
        other_factor = TOTP.activate(other, generate_totp_secret()).instance
        self.record("mfa", id=other_factor.pk, type="totp")
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

    def test_expired_admin_post_has_no_side_effect_or_pending_replay(self):
        auth, _ = self.primary()
        self.record("mfa", at=time.time() - 901, id=auth.pk, type="totp")
        response = self.client.post(reverse("admin:auth_group_add"), {"name": "must-not-be-created", "_save": "Save"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Group.objects.filter(name="must-not-be-created").exists())
        self.assertNotIn("account_reauthentication_state", self.client.session)

    def test_password_reauthentication_cannot_change_or_view_staff_factors(self):
        self.primary()
        self.record("password", reauthenticated=True)
        for name in ("mfa_index", "mfa_view_recovery_codes", "mfa_generate_recovery_codes", "mfa_deactivate_totp"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        response = self.client.post(reverse("mfa_deactivate_totp"))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Authenticator.objects.filter(user=self.user, type="totp").exists())

    def test_staff_cannot_remove_last_primary_factor_even_after_real_mfa(self):
        factor, secret = self.primary()
        self.confirm(secret)
        response = self.client.post(reverse("mfa_deactivate_totp"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Authenticator.objects.filter(pk=factor.pk).exists())
        self.assertFalse(admin.site.is_registered(Authenticator))

    def test_real_recovery_code_is_encrypted_single_use_and_needs_live_primary(self):
        factor, _ = self.primary()
        recovery = RecoveryCodes.activate(self.user)
        code = recovery.get_unused_codes()[0]
        self.assertNotIn(code, str(recovery.instance.data))
        response = self.client.post(reverse("mfa_reauthenticate"), {"code": code, "next": reverse("admin:index")})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.client.logout()
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse("mfa_reauthenticate"), {"code": code}).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        self.record("mfa", id=recovery.instance.pk, type="recovery_codes")
        factor.delete()
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 403)

    def test_nonstaff_cannot_use_admin_even_with_mfa(self):
        _, secret = self.primary()
        self.confirm(secret)
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 403)

    def test_grant_staff_role_only_promotes_existing_active_identity(self):
        other = User.objects.create_user(username="bootstrap", password="old-test-password")
        SocialAccount.objects.create(user=other, provider="github", uid="operator-id")
        with self.assertRaises(CommandError):
            call_command("grant_staff_role", provider="github", uid="missing-id", stdout=Mock())
        call_command("grant_staff_role", provider="github", uid="operator-id", superuser=True, stdout=Mock())
        other.refresh_from_db()
        self.assertTrue(other.is_staff and other.is_superuser)
        self.assertFalse(other.has_usable_password())
        self.assertFalse(Authenticator.objects.filter(user=other).exists())

    def test_recovery_codes_page_continues_to_admin_once_the_codes_are_saved(self):
        _, secret = self.primary()
        self.confirm(secret)
        RecoveryCodes.activate(self.user)
        response = self.client.get(reverse("mfa_view_recovery_codes"))
        self.assertContains(response, 'id="codes_saved" required')
        self.assertContains(response, 'action="' + reverse("admin:index") + '"')
        self.assertContains(response, "Continue to administration")
        self.assertContains(response, 'href="' + reverse("mfa_index") + '"')


class AccountMethodSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="author")
        self.account = SocialAccount.objects.create(user=self.user, provider="github", uid="known-account")
        SocialAccount.objects.create(user=self.user, provider="gitlab", uid="backup-account")
        self.client.force_login(self.user)

    def fresh_signin(self, at=None):
        request = RequestFactory().get("/")
        request.session = self.client.session
        record_authentication(request, self.user, "socialaccount", provider="github", uid=self.account.uid)
        if at is not None:
            request.session["account_authentication_methods"][-1]["at"] = at
        request.session.save()

    def remote_session(self, body):
        response = Mock(status_code=200)
        response.json.return_value = body
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value = response
        return session

    def test_stale_session_can_read_but_cannot_link_or_disconnect(self):
        self.fresh_signin(time.time() - 301)
        self.assertEqual(self.client.get(reverse("socialaccount_connections")).status_code, 200)
        self.assertEqual(self.client.post(reverse("socialaccount_connections"), {"account": self.account.pk}).status_code, 403)
        self.assertEqual(self.client.post("/accounts/gitlab/login/", {"process": "connect"}).status_code, 403)
        self.assertEqual(self.client.post("/accounts/gitlab/login/?process=connect").status_code, 403)
        self.assertTrue(SocialAccount.objects.filter(pk=self.account.pk).exists())

    def test_stale_session_cannot_add_email_signin_method(self):
        self.fresh_signin(time.time() - 301)
        response = self.client.post(reverse("account_email"), {"action_add": "", "email": "untrusted@example.org"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(EmailAddress.objects.filter(user=self.user, email="untrusted@example.org").exists())

    def test_fresh_existing_signin_permits_disconnect(self):
        self.fresh_signin()
        self.assertEqual(self.client.post(reverse("socialaccount_connections"), {"account": self.account.pk}).status_code, 302)
        self.assertFalse(SocialAccount.objects.filter(pk=self.account.pk).exists())

    def test_connect_callback_rechecks_original_account_freshness(self):
        self.fresh_signin()
        start = self.client.post("/accounts/gitlab/login/", {"process": "connect"})
        state = parse_qs(urlsplit(start.url).query)["state"][0]
        session = self.remote_session({"id": 999, "username": "new-account"})
        with patch("time.time", return_value=time.time() + 301), patch("allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token", return_value={"access_token": "temporary"}), patch("allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_requests_session", return_value=session):
            response = self.client.get("/accounts/gitlab/login/callback/", {"state": state, "code": "test-code"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SocialAccount.objects.filter(uid="999").exists())
        self.assertEqual(self.client.session["account_authentication_methods"][-1]["uid"], self.account.uid)
