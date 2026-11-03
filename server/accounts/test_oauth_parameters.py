"""Request parameters must not change the configured OAuth consent or app."""
from urllib.parse import parse_qs, urlencode, urlsplit

from django.test import TestCase
from django.urls import reverse


class FixedOAuthParametersTests(TestCase):
    def assert_rejected(self, path, data=None, method="post"):
        response = getattr(self.client, method)(path, data or {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content, b"This sign-in request is invalid.")
        self.assertNotIn("Location", response)
        self.assertNotIn("socialaccount_states", self.client.session)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_github_query_cannot_request_repository_and_email_access(self):
        self.assert_rejected("/accounts/github/login/?scope=repo,user:email")

    def test_orcid_query_cannot_expand_authentication_consent(self):
        self.assert_rejected("/accounts/orcid/login/?scope=/read-limited")

    def test_auth_parameters_cannot_replace_client_or_callback(self):
        query = urlencode({"auth_params": "client_id=other-app&redirect_uri=https%3A%2F%2Fattacker.invalid%2Fcallback&scope=repo"})
        self.assert_rejected("/accounts/github/login/?" + query)

    def test_all_configured_providers_reject_get_and_post_presence_even_empty(self):
        routes = [reverse(name) for name in ("orcid_login", "github_login", "gitlab_login")]
        routes.append(reverse("openid_connect_login", kwargs={"provider_id": "institution"}))
        for route in routes:
            for key in ("scope", "auth_params"):
                with self.subTest(route=route, key=key, source="GET"):
                    self.assert_rejected(route + "?" + key + "=", method="get")
                with self.subTest(route=route, key=key, source="POST"):
                    self.assert_rejected(route, {key: ""})

    def test_ordinary_signin_still_uses_configured_github_application(self):
        response = self.client.post(reverse("github_login"))
        self.assertEqual(response.status_code, 302)
        target = urlsplit(response.url)
        params = parse_qs(target.query, keep_blank_values=True)
        self.assertEqual(target.hostname, "github.com")
        self.assertEqual(params["client_id"], ["test-github"])
        self.assertEqual(params["redirect_uri"], ["http://testserver/accounts/github/login/callback/"])
        self.assertEqual(params["scope"], [""])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertTrue(params["state"][0])
