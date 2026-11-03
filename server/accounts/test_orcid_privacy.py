"""Use real OAuth routes; mock only the remote code exchange and profile fetch."""
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase


class MinimalOrcidTests(TestCase):
    def setUp(self):
        cache.clear()

    def complete(self, payload, **extra_query):
        start = self.client.post("/accounts/orcid/login/")
        self.assertEqual(start.status_code, 302)
        state = parse_qs(urlsplit(start.url).query)["state"][0]
        with patch("allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token", return_value=payload), patch("allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_requests_session") as profile_session:
            response = self.client.get("/accounts/orcid/login/callback/", {"code": "test-code", "state": state, **extra_query})
        profile_session.assert_not_called()
        return response

    def test_token_identifier_is_sufficient_and_profile_is_never_requested(self):
        response = self.complete({"access_token": "temporary-test-token", "refresh_token": "temporary-refresh", "orcid": "0000-0002-1825-0097", "name": "Discard this name"})
        self.assertEqual(response.status_code, 302)
        account = SocialAccount.objects.get()
        self.assertEqual(account.uid, "0000-0002-1825-0097")
        self.assertEqual(account.extra_data, {})
        self.assertEqual(account.user.email, "")
        self.assertEqual(account.user.first_name, "")
        self.assertEqual(account.user.last_name, "")
        self.assertFalse(EmailAddress.objects.exists())
        self.assertFalse(SocialToken.objects.exists())

    def test_missing_or_malformed_token_identifier_fails_closed(self):
        for identifier in (None, "", [], "https://orcid.org/0000-0002-1825-0097", "0000-0002-1825-0098", "0000-0002-1825-0097/extra"):
            with self.subTest(identifier=identifier):
                cache.clear()
                response = self.complete({"access_token": "temporary-test-token", "orcid": identifier}, orcid="0000-0002-1825-0097")
                self.assertEqual(response.status_code, 401)
                self.assertNotIn("_auth_user_id", self.client.session)
                self.assertFalse(get_user_model().objects.exists())

    def test_browser_identifier_cannot_override_authenticated_identifier(self):
        response = self.complete({"access_token": "temporary-test-token", "orcid": "0000-0002-1825-0097"}, orcid="0000-0001-5109-3700")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SocialAccount.objects.get().uid, "0000-0002-1825-0097")

    def test_invalid_state_never_reaches_token_exchange(self):
        with patch("allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token") as exchange:
            self.client.get("/accounts/orcid/login/callback/", {"code": "forged", "state": "forged", "orcid": "0000-0002-1825-0097"})
        exchange.assert_not_called()
        self.assertFalse(get_user_model().objects.exists())
