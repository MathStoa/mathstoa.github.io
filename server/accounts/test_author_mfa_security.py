"""Optional author MFA must not let a stolen session enroll or replace a factor."""
import time
from unittest.mock import patch

from allauth.account.internal.flows.login import record_authentication
from allauth.account.models import EmailAddress
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

    def test_recovery_codes_page_leads_on_once_the_codes_are_saved(self):
        self.record("socialaccount", provider="github", uid="existing-author")
        self.client.get(reverse("mfa_activate_totp"))
        secret = self.client.session["mfa.totp.secret"]
        response = self.client.post(reverse("mfa_activate_totp"), {"code": self.code(secret)})
        self.assertEqual(response.url, reverse("mfa_view_recovery_codes"))
        self.confirm(secret)
        codes = Authenticator.objects.get(user=self.user, type="recovery_codes").wrap().get_unused_codes()
        response = self.client.get(reverse("mfa_view_recovery_codes"))
        self.assertContains(response, codes[0])
        self.assertContains(response, 'id="codes_saved" required')
        self.assertContains(response, 'action="' + reverse("mfa_index") + '"')
        self.assertContains(response, ">Continue</button>")
        # Shown once: the next visit offers a new set instead of the codes.
        response = self.client.get(reverse("mfa_view_recovery_codes"))
        self.assertNotContains(response, codes[0])
        self.assertContains(response, 'href="' + reverse("mfa_generate_recovery_codes") + '"')
        self.assertContains(response, 'href="' + reverse("mfa_index") + '"')

    def site_page(self, name):
        """The page renders in the site's layout, in its words, and leads back to the account page."""
        response = self.client.get(reverse(name))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="' + reverse("socialaccount_connections") + '"')
        self.assertNotIn("wo-factor", response.content.decode().lower())
        return response

    def test_authenticator_pages_use_the_site_layout_and_lead_back_to_the_account(self):
        self.record("socialaccount", provider="github", uid="existing-author")
        response = self.site_page("mfa_index")
        self.assertContains(response, "<h1>Your authenticator</h1>")
        self.assertContains(response, 'href="' + reverse("mfa_activate_totp") + '"')
        self.assertNotContains(response, 'href="' + reverse("mfa_deactivate_totp") + '"')
        response = self.site_page("mfa_activate_totp")
        secret = self.client.session["mfa.totp.secret"]
        self.assertContains(response, 'src="data:image/svg+xml;base64,')
        self.assertContains(response, 'value="' + secret + '"')
        self.assertContains(response, 'name="code"')
        self.assertContains(response, 'action="' + reverse("mfa_activate_totp") + '"')
        self.assertContains(response, ">Finish setup</button>")
        self.client.post(reverse("mfa_activate_totp"), {"code": self.code(secret)})
        self.confirm(secret)
        response = self.site_page("mfa_index")
        self.assertContains(response, "Your authenticator app is set up. Added on")
        self.assertContains(response, 'href="' + reverse("mfa_deactivate_totp") + '"')
        self.assertContains(response, "You have 10 of 10 recovery codes left.")
        self.assertContains(response, 'href="' + reverse("mfa_generate_recovery_codes") + '"')
        for name, button in (("mfa_deactivate_totp", "Remove the authenticator"), ("mfa_generate_recovery_codes", "Generate new codes")):
            with self.subTest(name=name):
                response = self.site_page(name)
                self.assertContains(response, 'action="' + reverse(name) + '"')
                self.assertContains(response, ">" + button + "</button>")
                self.assertContains(response, 'href="' + reverse("mfa_index") + '"')
        self.assertContains(response, "You still have 10 unused recovery codes.")

    def test_authenticator_challenge_page_keeps_the_destination_and_leads_back(self):
        _, secret = self.factor()
        response = self.client.get(reverse("mfa_index"))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response.url)
        self.assertContains(response, "<h1>Confirm with your authenticator</h1>")
        self.assertContains(response, 'name="next" value="' + reverse("mfa_index") + '"')
        self.assertContains(response, 'name="code"')
        self.assertContains(response, 'href="' + reverse("socialaccount_connections") + '"')
        self.assertNotIn("wo-factor", response.content.decode().lower())
        response = self.client.post(reverse("mfa_reauthenticate"), {"code": "not-a-code", "next": reverse("mfa_index")})
        self.assertContains(response, "Incorrect code.")
        response = self.client.post(reverse("mfa_reauthenticate"), {"code": self.code(secret), "next": reverse("mfa_index")})
        self.assertRedirects(response, reverse("mfa_index"))

    def test_signing_in_with_an_authenticator_asks_for_its_code_on_a_site_page(self):
        _, secret = self.factor()
        self.user.email = "author@example.org"
        self.user.save()
        EmailAddress.objects.create(user=self.user, email=self.user.email, verified=True, primary=True)
        self.client.logout()
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="123456"):
            self.client.post(reverse("account_request_login_code"), {"email": self.user.email})
        response = self.client.post(reverse("account_confirm_login_code"), {"code": "123456"})
        self.assertRedirects(response, reverse("mfa_authenticate"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.client.get(reverse("mfa_authenticate"))
        self.assertContains(response, "<h1>Enter your authenticator code</h1>")
        self.assertContains(response, 'action="' + reverse("mfa_authenticate") + '"')
        self.assertContains(response, 'action="' + reverse("account_logout") + '"')
        self.assertNotIn("wo-factor", response.content.decode().lower())
        response = self.client.post(reverse("mfa_authenticate"), {"code": self.code(secret)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
