"""Organisation approval is enforced through every account entrance."""
from unittest.mock import patch

from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from .adapters import AccountAdapter, MinimalSocialAccountAdapter
from .models import OrganisationAccount


class OrganisationLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="organisation-test", email="team@example.org", is_active=False,
        )
        EmailAddress.objects.create(user=self.user, email=self.user.email, primary=True, verified=False)
        self.organisation = OrganisationAccount.objects.create(
            user=self.user, name="Test Research Group", email=self.user.email,
            website="https://example.org", description="An independent research group.",
            representative_name="Example Representative", authority="I run the group.",
        )

    def activate(self):
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(status="approved")
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=True)
        self.user.refresh_from_db()

    def request_code(self, *, path=None, email=None, client=None):
        client = client or self.client
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="123456"):
            return client.post(path or reverse("organisation_login"), {"email": email or self.user.email})

    def confirm_code(self, client=None):
        return (client or self.client).post(reverse("account_confirm_login_code"), {"code": "123456"})

    def test_approved_organisation_uses_existing_single_use_browser_bound_code(self):
        self.activate()
        response = self.request_code(email="TEAM@example.org")
        self.assertRedirects(response, reverse("account_confirm_login_code"), fetch_redirect_response=False)
        self.assertIn("123456", mail.outbox[-1].body)
        self.assertNotIn("_auth_user_id", self.client.session)
        stranger = Client()
        self.confirm_code(stranger)
        self.assertNotIn("_auth_user_id", stranger.session)
        self.confirm_code()
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertTrue(EmailAddress.objects.get(user=self.user).verified)
        self.client.logout()
        self.confirm_code()
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_pending_and_rejected_cannot_sign_in_even_if_active_flag_is_changed(self):
        for status in ("pending", "rejected"):
            for path in (reverse("organisation_login"), reverse("account_request_login_code")):
                with self.subTest(status=status, path=path):
                    cache.clear()
                    self.client.logout()
                    OrganisationAccount.objects.filter(pk=self.organisation.pk).update(status=status)
                    get_user_model().objects.filter(pk=self.user.pk).update(is_active=True)
                    self.request_code(path=path)
                    self.confirm_code()
                    self.assertNotIn("_auth_user_id", self.client.session)
                    self.assertEqual(self.client.get(reverse("submissions:create")).status_code, 302)

    def test_pending_organisation_generic_signup_does_not_create_active_duplicate(self):
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_email_verification_code", return_value="123456"):
            self.client.post(reverse("account_signup"), {"email": self.user.email.upper()})
        self.client.post(reverse("account_email_verification_sent"), {"code": "123456"})
        self.assertEqual(get_user_model().objects.count(), 1)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_personal_and_unknown_email_do_not_receive_organisation_login_code(self):
        personal = get_user_model().objects.create_user(username="personal", email="person@example.org")
        EmailAddress.objects.create(user=personal, email=personal.email, primary=True, verified=True)
        for email in (personal.email, "unknown@example.org", self.user.email):
            with self.subTest(email=email):
                cache.clear()
                self.client.logout()
                with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code") as generate:
                    response = self.client.post(reverse("organisation_login"), {"email": email})
                generate.assert_not_called()
                self.assertRedirects(response, reverse("account_confirm_login_code"), fetch_redirect_response=False)
                self.confirm_code()
                self.assertNotIn("_auth_user_id", self.client.session)
        # The ordinary entrance continues to accept a personal account.
        self.client.logout()
        cache.clear()
        self.request_code(path=reverse("account_request_login_code"), email=personal.email)
        self.confirm_code()
        self.assertEqual(int(self.client.session["_auth_user_id"]), personal.pk)

    def test_moderation_change_after_code_request_blocks_completion(self):
        self.activate()
        self.request_code()
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(status="rejected")
        self.confirm_code()
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_moderation_change_revokes_existing_session_before_action(self):
        self.activate()
        self.client.force_login(self.user)
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(status="rejected")
        response = self.client.post(reverse("submissions:create"), {"title": "Should not be created"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(self.user.owned_submissions.exists())

    def test_final_login_hook_rechecks_moderation_after_a_challenge(self):
        self.activate()
        request = RequestFactory().get("/")
        request.session = self.client.session
        request.user = AnonymousUser()
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(status="rejected")
        with self.assertRaises(ImmediateHttpResponse):
            AccountAdapter(request).login(request, self.user)
        self.assertNotIn("_auth_user_id", request.session)

    def test_organisation_mailbox_and_provider_links_cannot_be_changed(self):
        self.activate()
        self.request_code()
        self.confirm_code()
        for provider in ("orcid", "github", "gitlab"):
            self.assertEqual(self.client.post(f"/accounts/{provider}/login/", {"process": "connect"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("account_email"), {"email": "different@example.org", "action_add": ""}).status_code, 403)
        self.assertFalse(SocialAccount.objects.filter(user=self.user).exists())
        self.assertEqual(list(EmailAddress.objects.filter(user=self.user).values_list("email", flat=True)), [self.user.email])

    def test_oauth_callback_cannot_link_an_organisation_but_verification_can_continue(self):
        self.activate()
        request = RequestFactory().get("/accounts/github/login/callback/")
        request.user = self.user
        request.session = self.client.session
        sociallogin = SocialLogin(
            user=get_user_model()(), account=SocialAccount(provider="github", uid="99"),
        )
        sociallogin.state = {"process": "connect"}
        adapter = MinimalSocialAccountAdapter(request)
        with self.assertRaises(ImmediateHttpResponse) as rejected:
            adapter.pre_social_login(request, sociallogin)
        self.assertEqual(rejected.exception.response.status_code, 403)
        # The repository-verification receiver validates this signed state and
        # always exits before a social account can be linked.
        sociallogin.state["data"] = {"mathstoa_verification": "attempt"}
        self.assertIsNone(adapter.pre_social_login(request, sociallogin))

    def test_login_requires_csrf_and_obeys_email_setting(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("organisation_login"), {"email": self.user.email}).status_code, 403)
        with override_settings(ORGANISATION_LOGIN_ENABLED=False):
            self.assertContains(self.client.get(reverse("organisation_login")), "unavailable")
            self.assertEqual(self.client.post(reverse("organisation_login"), {"email": self.user.email}).status_code, 404)

    @override_settings(ACCOUNT_RATE_LIMITS={"request_login_code": "1/h/key"})
    def test_login_rate_limit_is_shared_with_standard_email_entrance(self):
        self.activate()
        self.request_code()
        self.client.logout()
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code") as generate:
            response = self.client.post(reverse("account_request_login_code"), {"email": self.user.email})
        generate.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many")

    @override_settings(EMAIL_LOGIN_ENABLED=False, ORGANISATION_LOGIN_ENABLED=True)
    def test_organisation_only_mode_can_sign_in_without_enabling_personal_email(self):
        self.activate()
        response = self.request_code()
        self.assertRedirects(response, reverse("account_confirm_login_code"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("account_confirm_login_code")).status_code, 200)
        self.confirm_code()
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertTrue(EmailAddress.objects.get(user=self.user).verified)

    @override_settings(EMAIL_LOGIN_ENABLED=False, ORGANISATION_LOGIN_ENABLED=True)
    def test_organisation_only_mode_never_opens_personal_email_entrances(self):
        personal = get_user_model().objects.create_user(username="personal-only", email="personal-only@example.org")
        EmailAddress.objects.create(user=personal, email=personal.email, primary=True, verified=True)
        for name in ("account_signup", "account_email", "account_request_login_code"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 404)
                self.assertEqual(self.client.post(reverse(name), {"email": personal.email}).status_code, 404)
        self.assertEqual(self.client.get(reverse("account_login")).status_code, 200)
        for data in ({"login": personal.email}, {"login": personal.email, "action": "login_by_code"}, {"email": personal.email}):
            self.assertEqual(self.client.post(reverse("account_login"), data).status_code, 404)
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code") as generate:
            self.request_code(email=personal.email)
        generate.assert_not_called()
        self.confirm_code()
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(get_user_model().objects.count(), 2)

    @override_settings(EMAIL_LOGIN_ENABLED=False, ORGANISATION_LOGIN_ENABLED=True)
    def test_organisation_only_mode_allows_registration_but_does_not_sign_in(self):
        response = self.client.get(reverse("organisation_register"))
        self.assertContains(response, "Send for moderation")
        response = self.client.post(reverse("organisation_register"), {
            "name": "Second Organisation", "website": "https://second.example.org",
            "description": "Mathematical research.", "email": "second@example.org",
            "representative_name": "Example Director", "authority": "Listed as director on our website.",
        })
        self.assertEqual(response.status_code, 302)
        organisation = OrganisationAccount.objects.get(email="second@example.org")
        self.assertEqual(organisation.status, "pending")
        self.assertFalse(organisation.user.is_active)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(EMAIL_LOGIN_ENABLED=False, ORGANISATION_LOGIN_ENABLED=True)
    def test_organisation_only_confirmation_rejects_unrelated_pending_login(self):
        personal = get_user_model().objects.create_user(username="previous-personal", email="previous@example.org")
        EmailAddress.objects.create(user=personal, email=personal.email, primary=True, verified=True)
        with override_settings(EMAIL_LOGIN_ENABLED=True):
            self.request_code(path=reverse("account_request_login_code"), email=personal.email)
        self.assertEqual(self.confirm_code().status_code, 404)
        self.assertNotIn("_auth_user_id", self.client.session)
        # A previous generic organisation code is not the dedicated entrance.
        self.activate()
        with override_settings(EMAIL_LOGIN_ENABLED=True):
            self.request_code(path=reverse("account_request_login_code"))
        self.assertEqual(self.confirm_code().status_code, 404)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(EMAIL_LOGIN_ENABLED=False, ORGANISATION_LOGIN_ENABLED=True)
    def test_organisation_only_confirmation_resend_preserves_bound_flow(self):
        self.activate()
        self.request_code()
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="654321"):
            response = self.client.post(reverse("account_confirm_login_code"), {"action": "resend"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("654321", mail.outbox[-1].body)
        self.confirm_code()
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.post(reverse("account_confirm_login_code"), {"code": "654321"})
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    @override_settings(EMAIL_LOGIN_ENABLED=False, ORGANISATION_LOGIN_ENABLED=True)
    def test_organisation_only_confirmation_rechecks_activation_and_setting(self):
        self.activate()
        self.request_code()
        with override_settings(ORGANISATION_LOGIN_ENABLED=False):
            self.assertEqual(self.confirm_code().status_code, 404)
        OrganisationAccount.objects.filter(pk=self.organisation.pk).update(status="rejected")
        self.assertEqual(self.confirm_code().status_code, 404)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(EMAIL_LOGIN_ENABLED=False, ORGANISATION_LOGIN_ENABLED=True, SOCIALACCOUNT_PROVIDERS={})
    def test_organisation_only_login_page_does_not_offer_personal_email_signup(self):
        response = self.client.get(reverse("account_login"))
        self.assertContains(response, reverse("organisation_login"))
        self.assertNotContains(response, "Create an account with email")
        self.assertNotContains(response, "Email me a sign-in code")
        self.assertNotContains(response, "Account sign-in is not available yet")
