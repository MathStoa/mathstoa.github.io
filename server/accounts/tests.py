"""Exercise actual sign-in routes, with only remote identity services mocked."""
import time
import jwt
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.contrib.auth import get_user_model
from django.core import mail
from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

User = get_user_model()


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()

    def remote_session(self, body):
        response = Mock(status_code=200)
        response.json.return_value = body
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.return_value = response
        return session

    def oauth(self, provider, profile, *, connect=False, token=None):
        start = self.client.post(f"/accounts/{provider}/login/", {"process": "connect" if connect else "login"})
        self.assertEqual(start.status_code, 302)
        query = parse_qs(urlsplit(start.url).query)
        state = query["state"][0]
        token_data = token or {"access_token": "never-store-this-token"}
        if provider == "orcid":
            token_data["orcid"] = profile["orcid-identifier"]["path"]
        session = self.remote_session(profile)
        with patch("allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token", return_value=token_data), patch("allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_requests_session", return_value=session):
            response = self.client.get(f"/accounts/{provider}/login/callback/", {"code": "test-code", "state": state})
        self.assertEqual(response.status_code, 302, response.content.decode()[:1000])
        return state, query

    def test_orcid_login_without_email_and_repeat_login(self):
        profile = {"orcid-identifier": {"path": "0000-0002-1825-0097"}, "person": {"name": {"given-names": {"value": "A"}}, "emails": {"email": [{"email": "private@example.org"}]}}}
        self.oauth("orcid", profile)
        account = SocialAccount.objects.get()
        user_id = account.user_id
        self.assertEqual(account.uid, "0000-0002-1825-0097")
        self.assertEqual(account.extra_data, {})
        self.assertEqual(account.user.email, "")
        self.assertFalse(account.user.has_usable_password())
        self.assertFalse(EmailAddress.objects.exists())
        self.assertFalse(SocialToken.objects.exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user_id)
        self.client.logout()
        self.oauth("orcid", profile)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user_id)

    def test_github_and_gitlab_login_use_stable_ids_not_emails(self):
        for provider in ("github", "gitlab"):
            with self.subTest(provider=provider):
                self.client.logout()
                self.oauth(provider, {"id": 12345, "login": "researcher", "username": "researcher", "name": "Author", "email": "private@example.org", "bio": "discard"})
                account = SocialAccount.objects.get(provider=provider)
                self.assertEqual(account.uid, "12345")
                self.assertEqual(account.extra_data, {})
                self.assertEqual(account.user.email, "")
        self.assertEqual(User.objects.count(), 2)
        self.assertFalse(SocialToken.objects.exists())

    def test_unauthenticated_or_replayed_callback_cannot_log_in(self):
        response = self.client.get("/accounts/github/login/callback/", {"code": "forged", "state": "forged"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(User.objects.count(), 0)
        state, query = self.oauth("github", {"id": 19})
        self.assertIn("code_challenge", query)
        self.client.logout()
        self.client.get("/accounts/github/login/callback/", {"code": "replay", "state": state})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_institutional_oidc_sign_in_validates_issuer_and_audience(self):
        configuration = {"issuer": "https://university.example", "authorization_endpoint": "https://university.example/authorize", "token_endpoint": "https://university.example/token", "jwks_uri": "https://university.example/keys"}
        session = self.remote_session(configuration)
        for audience in ("wrong-client", "test-university"):
            with self.subTest(audience=audience):
                with patch("allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_requests_session", return_value=session):
                    response = self.client.post("/accounts/oidc/institution/login/")
                    query = parse_qs(urlsplit(response.url).query)
                    self.assertIn("code_challenge", query)
                    self.assertEqual(query["scope"], ["openid"])
                    claims = {"iss": "https://university.example", "sub": "stable-university-id", "aud": audience, "exp": int(time.time()) + 120, "iat": int(time.time()), "email": "private@university.example"}
                    token = jwt.encode(claims, "test-identity-server-signing-key-is-long", algorithm="HS256")
                    with patch("allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token", return_value={"access_token": "temporary", "id_token": token}):
                        response = self.client.get("/accounts/oidc/institution/login/callback/", {"code": "code", "state": query["state"][0]})
                if audience == "wrong-client":
                    self.assertNotIn("_auth_user_id", self.client.session)
                else:
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(SocialAccount.objects.get(provider="institution").uid, "stable-university-id")
                    self.assertEqual(SocialAccount.objects.get().extra_data, {})

    def test_connect_provider_requires_existing_signed_in_account(self):
        user = User.objects.create_user(username="existing", email="same@example.org")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        # Matching email from a provider is never proof of control of this account.
        self.oauth("github", {"id": 55, "email": user.email})
        self.assertNotEqual(SocialAccount.objects.get().user_id, user.pk)
        self.client.logout()
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="123456"):
            self.client.post("/accounts/login/code/", {"email": user.email})
        self.client.post("/accounts/login/code/confirm/", {"code": "123456"})
        self.oauth("gitlab", {"id": 88}, connect=True)
        self.assertEqual(SocialAccount.objects.get(provider="gitlab").user_id, user.pk)

    def test_login_provider_and_logout_require_csrf_protected_post(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/accounts/github/login/").status_code, 403)
        response = client.get("/accounts/github/login/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Location", response)
        user = User.objects.create_user(username="signed-in")
        client.force_login(user)
        client.get("/accounts/logout/")
        self.assertIn("_auth_user_id", client.session)
        self.assertEqual(client.post("/accounts/logout/").status_code, 403)

    def test_existing_email_login_code_is_single_use_and_bound_to_browser(self):
        user = User.objects.create_user(username="email-user", email="author@example.org")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="123456"):
            response = self.client.post("/accounts/login/code/", {"email": user.email})
        self.assertRedirects(response, "/accounts/login/code/confirm/", fetch_redirect_response=False)
        self.assertIn("123456", mail.outbox[-1].body)
        stranger = Client()
        stranger.post("/accounts/login/code/confirm/", {"code": "123456"})
        self.assertNotIn("_auth_user_id", stranger.session)
        self.client.post("/accounts/login/code/confirm/", {"code": "000000"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.post("/accounts/login/code/confirm/", {"code": "123456"})
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.client.post("/accounts/logout/")
        self.client.post("/accounts/login/code/confirm/", {"code": "123456"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_email_signup_requires_verification_without_a_password(self):
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_email_verification_code", return_value="123456"):
            response = self.client.post("/accounts/signup/", {"email": "new@example.org"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        user = User.objects.get(email="new@example.org")
        self.assertFalse(user.has_usable_password())
        self.client.post("/accounts/confirm-email/", {"code": "123456"})
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertTrue(EmailAddress.objects.get(user=user).verified)

    def test_no_password_routes_and_disabled_email_cannot_be_used(self):
        self.assertEqual(self.client.get("/accounts/password/set/").status_code, 404)
        with override_settings(EMAIL_LOGIN_ENABLED=False, SOCIALACCOUNT_PROVIDERS={}):
            self.assertEqual(self.client.get("/accounts/orcid/login/").status_code, 404)
            self.assertEqual(self.client.post("/accounts/signup/", {"email": "blocked@example.org"}).status_code, 404)
            self.assertEqual(self.client.post("/accounts/login/code/", {"email": "blocked@example.org"}).status_code, 404)
            response = self.client.get("/accounts/login/")
            self.assertContains(response, "Account sign-in is not available yet")
        self.assertFalse(User.objects.exists())

    def test_email_code_attempt_limit_and_expiry(self):
        user = User.objects.create_user(username="limited", email="limited@example.org")
        EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="123456"):
            self.client.post("/accounts/login/code/", {"email": user.email})
        for attempt in range(3):
            self.client.post("/accounts/login/code/confirm/", {"code": "000000"})
        self.client.post("/accounts/login/code/confirm/", {"code": "123456"})
        self.assertNotIn("_auth_user_id", self.client.session)
        cache.clear()
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="654321"):
            self.client.post("/accounts/login/code/", {"email": user.email})
        with patch("time.time", return_value=time.time() + 181):
            self.client.post("/accounts/login/code/confirm/", {"code": "654321"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_disconnect_prevents_lockout_and_allows_verified_email_alternative(self):
        user = User.objects.create_user(username="disconnect")
        account = SocialAccount.objects.create(user=user, provider="github", uid="99")
        self.oauth("github", {"id": 99})
        self.client.post(reverse("socialaccount_connections"), {"account": account.pk})
        self.assertTrue(SocialAccount.objects.filter(pk=account.pk).exists())
        EmailAddress.objects.create(user=user, email="recover@example.org", verified=True, primary=True)
        self.client.post(reverse("socialaccount_connections"), {"account": account.pk})
        self.assertFalse(SocialAccount.objects.filter(pk=account.pk).exists())

    def test_disabled_provider_is_not_a_recovery_method(self):
        user = User.objects.create_user(username="disabled-alternative")
        github = SocialAccount.objects.create(user=user, provider="github", uid="github-42")
        SocialAccount.objects.create(user=user, provider="gitlab", uid="gitlab-42")
        self.oauth("github", {"id": "github-42"})
        with override_settings(EMAIL_LOGIN_ENABLED=False, SOCIALACCOUNT_PROVIDERS={"github": settings.SOCIALACCOUNT_PROVIDERS["github"]}):
            self.client.post(reverse("socialaccount_connections"), {"account": github.pk})
        self.assertTrue(SocialAccount.objects.filter(pk=github.pk).exists())

    def test_private_pages_are_not_cached_and_public_files_are_allowlisted(self):
        response = self.client.get("/accounts/login/")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertIn("form-action 'self' https://orcid.org https://github.com", response["Content-Security-Policy"])
        for path in ("/settings.py", "/.env", "/assets/../../server/config/settings.py"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/feed.xml").status_code, 200)
