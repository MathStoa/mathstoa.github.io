"""Optional author MFA must not let a stolen session enroll or replace a factor."""
import time

from allauth.account.internal.flows.login import record_authentication
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, generate_totp_secret, hotp_value
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse


class AuthorMFASecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="optional-mfa-author")
        self.client.force_login(self.user)

    def record(self, method, **extra):
        request = RequestFactory().get("/")
        request.session = self.client.session
        record_authentication(request, self.user, method, **extra)
        request.session.save()

    def code(self, secret):
        return format_hotp_value(hotp_value(secret, int(time.time()) // 30))

    def factor(self):
        secret = generate_totp_secret()
        return TOTP.activate(self.user, secret).instance, secret

    def confirm(self, secret):
        response = self.client.post(reverse("mfa_reauthenticate"), {"code": self.code(secret)})
        self.assertEqual(response.status_code, 302)

    def test_stale_author_session_cannot_start_enrollment(self):
        self.assertEqual(self.client.get(reverse("mfa_activate_totp")).status_code, 403)
        self.assertNotIn("mfa.totp.secret", self.client.session)

    def test_stale_author_session_cannot_finish_pending_enrollment(self):
        secret = generate_totp_secret()
        session = self.client.session
        session["mfa.totp.secret"] = secret
        session.save()
        response = self.client.post(reverse("mfa_activate_totp"), {"code": self.code(secret)})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Authenticator.objects.filter(user=self.user).exists())

    def test_fresh_author_can_enroll_then_must_confirm_before_managing_mfa(self):
        self.record("socialaccount", provider="github", uid="existing-author")
        self.assertEqual(self.client.get(reverse("mfa_activate_totp")).status_code, 200)
        secret = self.client.session["mfa.totp.secret"]
        self.assertEqual(self.client.post(reverse("mfa_activate_totp"), {"code": self.code(secret)}).status_code, 302)
        self.assertTrue(Authenticator.objects.filter(user=self.user, type="totp").exists())
        response = self.client.get(reverse("mfa_index"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("mfa_reauthenticate")))
        self.confirm(secret)
        self.assertEqual(self.client.get(reverse("mfa_index")).status_code, 200)

    def test_author_password_record_cannot_bypass_factor_management(self):
        factor, _ = self.factor()
        self.record("password", reauthenticated=True)
        self.assertEqual(self.client.get(reverse("mfa_index")).status_code, 302)
        self.assertEqual(self.client.post(reverse("mfa_deactivate_totp")).status_code, 403)
        self.assertTrue(Authenticator.objects.filter(pk=factor.pk).exists())

    def test_author_may_remove_optional_factor_after_real_mfa(self):
        factor, secret = self.factor()
        self.confirm(secret)
        self.assertEqual(self.client.post(reverse("mfa_deactivate_totp")).status_code, 302)
        self.assertFalse(Authenticator.objects.filter(pk=factor.pk).exists())

    def test_author_with_factor_needs_mfa_as_well_as_fresh_signin_for_account_changes(self):
        _, secret = self.factor()
        first = SocialAccount.objects.create(user=self.user, provider="github", uid="author-first")
        SocialAccount.objects.create(user=self.user, provider="gitlab", uid="author-backup")
        self.record("socialaccount", provider="github", uid=first.uid)
        self.assertEqual(self.client.post("/accounts/github/login/?process=connect").status_code, 403)
        self.assertEqual(self.client.post(reverse("socialaccount_connections"), {"account": first.pk}).status_code, 403)
        self.assertTrue(SocialAccount.objects.filter(pk=first.pk).exists())
        self.confirm(secret)
        self.assertEqual(self.client.post(reverse("socialaccount_connections"), {"account": first.pk}).status_code, 302)
        self.assertFalse(SocialAccount.objects.filter(pk=first.pk).exists())

    def test_author_account_page_offers_optional_authenticator_settings(self):
        response = self.client.get(reverse("socialaccount_connections"))
        self.assertContains(response, 'href="' + reverse("mfa_index") + '"')
