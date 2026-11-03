"""Authorization UI, callback binding and irreversible proof invalidation."""
import hashlib
import base64
import io
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from requests import Response

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from submissions.models import Submission
from .models import RepositoryVerification, VerificationAttempt
from .signals import verify_github_callback
from .views import _rate_limit_response


@override_settings(ACCOUNT_RATE_LIMITS={"repository_verification": "5/m/user,10/h/user,5/m/key,10/h/key"})
class VerificationUITests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user(username="verification-owner")
        self.other = get_user_model().objects.create_user(username="verification-other")
        self.submission = Submission.objects.create(
            owner=self.owner, title="Research project", repository_url="https://github.com/owner/research",
        )
        self.client.force_login(self.owner)

    def url(self, name="overview"):
        return reverse("verification:" + name, args=[self.submission.pk])

    def attempt(self, method="file", **extra):
        values = {
            "submission": self.submission, "user": self.owner, "method": method,
            "repository_url": self.submission.repository_url, "repository_id": 123,
            "repository_owner_id": 456, "full_name": "owner/research",
            "token_hash": hashlib.sha256((str(VerificationAttempt.objects.count()) + "token").encode()).hexdigest(),
            "expires_at": timezone.now() + timedelta(hours=1),
        }
        values.update(extra)
        return VerificationAttempt.objects.create(**values)

    def proof(self, attempt=None):
        attempt = attempt or self.attempt()
        return RepositoryVerification.objects.create(
            attempt=attempt, submission=self.submission, user=self.owner, method=attempt.method,
            repository_url=attempt.repository_url, repository_id=attempt.repository_id,
            repository_owner_id=attempt.repository_owner_id, full_name=attempt.full_name,
            valid_until=timezone.now() + timedelta(days=1),
        )

    def store_document(self, attempt, text="file contents"):
        session = self.client.session
        key = f"mathstoa_verification_file:{self.owner.pk}:{self.submission.pk}"
        session[key] = {"attempt_id": str(attempt.pk), "content": text}
        session.save()
        return key

    def callback_request(self, user=None):
        request = RequestFactory().get("/accounts/github/login/callback/")
        request.user = self.owner if user is None else user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def callback(self, attempt, **extra):
        values = {
            "state": {"process": "connect", "data": {"mathstoa_verification": str(attempt.pk), "nonce": "raw-nonce"}},
            "account": SimpleNamespace(provider="github", uid="789"),
            "token": SimpleNamespace(token="private-oauth-token"),
        }
        values.update(extra)
        return SimpleNamespace(**values)

    def test_only_owner_can_view_or_perform_verification(self):
        self.submission.editors.add(self.other)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        for name in ("issue_file", "verify_file", "github_start"):
            self.assertEqual(self.client.post(self.url(name)).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_mutations_require_post_and_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        for name in ("issue_file", "verify_file", "github_start"):
            self.assertEqual(self.client.get(self.url(name)).status_code, 405)
            self.assertEqual(csrf_client.post(self.url(name)).status_code, 403)

    def test_file_display_escapes_content_and_prevents_caching(self):
        attempt = self.attempt()
        self.store_document(attempt, '</textarea><script>alert("test")</script>')
        response = self.client.get(self.url())
        self.assertContains(response, "&lt;/textarea&gt;&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("test")</script>')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(response, ".mathstoa-verification")

    def test_expired_file_content_is_removed(self):
        attempt = self.attempt(expires_at=timezone.now() - timedelta(seconds=1))
        key = self.store_document(attempt, "expired-code-do-not-display")
        response = self.client.get(self.url())
        self.assertNotContains(response, "expired-code-do-not-display")
        self.assertNotIn(key, self.client.session)

    @patch("repository_verification.views.challenge_document", return_value="generated document")
    @patch("repository_verification.views.start_attempt")
    def test_generate_file_displays_session_scoped_document(self, start, document):
        attempt = self.attempt()
        start.return_value = (attempt, "raw-file-token")
        response = self.client.post(self.url("issue_file"), follow=True)
        self.assertContains(response, "generated document")
        start.assert_called_once_with(self.submission, self.owner, "file")
        document.assert_called_once_with(attempt, "raw-file-token")

    @patch("repository_verification.views.check_file")
    def test_file_check_without_issued_document_does_not_verify(self, check):
        response = self.client.post(self.url("verify_file"), follow=True)
        self.assertContains(response, "Generate a verification file")
        check.assert_not_called()

    @patch("repository_verification.views.check_file")
    def test_successful_file_check_discards_displayed_token(self, check):
        attempt = self.attempt()
        key = self.store_document(attempt)
        response = self.client.post(self.url("verify_file"), follow=True)
        self.assertContains(response, "Repository authorization verified.")
        self.assertNotIn(key, self.client.session)
        check.assert_called_once_with(attempt, self.owner)

    @patch("repository_verification.views._github_provider", return_value=None)
    @patch("repository_verification.views.start_attempt")
    def test_unconfigured_github_falls_back_to_file(self, start, provider):
        response = self.client.post(self.url("github_start"), follow=True)
        self.assertContains(response, "GitHub verification is not configured yet")
        start.assert_not_called()

    @patch("repository_verification.views._github_provider")
    @patch("repository_verification.views.start_attempt")
    def test_github_state_binds_attempt_without_exposing_token_in_url(self, start, get_provider):
        attempt = self.attempt(method="github")
        start.return_value = (attempt, "raw-nonce")
        provider = Mock()
        provider.redirect.return_value = HttpResponseRedirect("https://github.com/login/oauth/authorize?state=opaque")
        get_provider.return_value = provider
        response = self.client.post(self.url("github_start"))
        kwargs = provider.redirect.call_args.kwargs
        self.assertEqual(kwargs["process"], "connect")
        self.assertEqual(kwargs["data"], {"mathstoa_verification": str(attempt.pk), "nonce": "raw-nonce"})
        self.assertNotIn("raw-nonce", response["Location"])

    @patch("repository_verification.signals.check_github")
    def test_ordinary_github_login_is_untouched(self, check):
        login = SimpleNamespace(state={"process": "login"})
        self.assertIsNone(verify_github_callback(None, self.callback_request(), login))
        check.assert_not_called()

    @patch("repository_verification.signals.check_github")
    def test_callback_checks_current_user_and_uid_then_stops_token_storage(self, check):
        attempt = self.attempt(method="github")
        with self.assertRaises(ImmediateHttpResponse) as response:
            verify_github_callback(None, self.callback_request(), self.callback(attempt))
        check.assert_called_once_with(attempt, self.owner, "raw-nonce", "private-oauth-token", expected_github_uid="789")
        self.assertEqual(response.exception.response["Location"], self.url())
        self.assertFalse(SocialToken.objects.exists())
        self.assertFalse(SocialAccount.objects.filter(provider="github").exists())

    @patch("repository_verification.signals.check_github")
    def test_callback_cannot_switch_local_account(self, check):
        attempt = self.attempt(method="github")
        for user in (self.other, AnonymousUser()):
            with self.subTest(user=user), self.assertRaises(ImmediateHttpResponse) as response:
                verify_github_callback(None, self.callback_request(user), self.callback(attempt))
            self.assertEqual(response.exception.response["Location"], reverse("submissions:list"))
        check.assert_not_called()

    @patch("repository_verification.signals.check_github")
    def test_other_providers_and_account_links_cannot_satisfy_github_flow(self, check):
        attempt = self.attempt(method="github")
        with self.assertRaises(ImmediateHttpResponse):
            verify_github_callback(None, self.callback_request(), self.callback(attempt, account=SimpleNamespace(provider="orcid", uid="789")))
        SocialAccount.objects.create(user=self.other, provider="github", uid="789")
        with self.assertRaises(ImmediateHttpResponse):
            verify_github_callback(None, self.callback_request(), self.callback(attempt))
        check.assert_not_called()

    @patch("repository_verification.signals.check_github", side_effect=ValidationError("This verification expired."))
    def test_callback_errors_do_not_expose_token(self, check):
        attempt = self.attempt(method="github")
        request = self.callback_request()
        with self.assertRaises(ImmediateHttpResponse) as response:
            verify_github_callback(None, request, self.callback(attempt))
        text = " ".join(str(item) for item in request._messages)
        self.assertIn("expired", text)
        self.assertNotIn("private-oauth-token", text)
        self.assertNotIn("private-oauth-token", response.exception.response["Location"])

    def test_changing_repository_away_and_back_cannot_restore_proof(self):
        attempt = self.attempt()
        proof = self.proof(attempt)
        original = self.submission.repository_url
        self.submission.repository_url = "https://github.com/owner/other"
        self.submission.save()
        self.submission.repository_url = original
        self.submission.save()
        attempt.refresh_from_db()
        proof.refresh_from_db()
        self.assertIsNotNone(attempt.revoked_at)
        self.assertIsNotNone(proof.revoked_at)

    def test_unsaved_repository_change_does_not_revoke_proof(self):
        proof = self.proof()
        self.submission.repository_url = "https://github.com/owner/other"
        self.submission.title = "Title correction"
        self.submission.save(update_fields=["title"])
        proof.refresh_from_db()
        self.assertIsNone(proof.revoked_at)

    def test_adding_or_removing_orcid_revokes_proof(self):
        proof = self.proof()
        account = SocialAccount.objects.create(user=self.owner, provider="orcid", uid="0000-0002-1825-0097")
        proof.refresh_from_db()
        self.assertIsNotNone(proof.revoked_at)
        newer = self.proof()
        account.delete()
        newer.refresh_from_db()
        self.assertIsNotNone(newer.revoked_at)

    def test_unchanged_orcid_login_does_not_revoke_proof(self):
        account = SocialAccount.objects.create(user=self.owner, provider="orcid", uid="0000-0002-1825-0097")
        proof = self.proof()
        account.save()
        proof.refresh_from_db()
        self.assertIsNone(proof.revoked_at)

    @patch("repository_verification.views.ratelimit.consume", return_value=False)
    @patch("repository_verification.views.start_attempt")
    @patch("repository_verification.views.check_file")
    def test_rate_limit_stops_all_post_methods_before_external_work(self, check, start, consume):
        self.store_document(self.attempt())
        for name in ("issue_file", "verify_file", "github_start"):
            with self.subTest(name=name):
                response = self.client.post(self.url(name), follow=True)
                self.assertContains(response, "Too many verification attempts")
        start.assert_not_called()
        check.assert_not_called()
        kwargs = consume.call_args.kwargs
        self.assertEqual(kwargs["user"], self.owner)
        self.assertEqual(kwargs["key"], f"user:{self.owner.pk}:submission:{self.submission.pk}")

    @patch("repository_verification.views.challenge_document", return_value="document")
    @patch("repository_verification.views.start_attempt")
    def test_burst_limit_is_shared_between_methods_but_not_accounts(self, start, document):
        start.return_value = (self.attempt(), "token")
        for _ in range(5):
            self.assertEqual(self.client.post(self.url("issue_file")).status_code, 302)
        response = self.client.post(self.url("github_start"), follow=True)
        self.assertContains(response, "Too many verification attempts")
        self.assertEqual(start.call_count, 5)
        second = Submission.objects.create(owner=self.other, title="Other research", repository_url="https://github.com/other/research")
        self.client.force_login(self.other)
        self.client.post(reverse("verification:issue_file", args=[second.pk]))
        self.assertEqual(start.call_count, 6)

    def test_hourly_limit_applies_after_burst_window_passes(self):
        request = self.callback_request()
        request.method = "POST"
        now = time.time()
        for number in range(10):
            with patch("allauth.core.internal.ratelimit.time.time", return_value=now + 70 * number):
                self.assertIsNone(_rate_limit_response(request, self.submission))
        with patch("allauth.core.internal.ratelimit.time.time", return_value=now + 700):
            self.assertEqual(_rate_limit_response(request, self.submission).status_code, 302)

    def test_github_browser_flow_binds_pkce_state_and_discards_credential(self):
        """Exercise real allauth and verification code, mocking HTTP only."""
        orcid = SocialAccount.objects.create(user=self.owner, provider="orcid", uid="0000-0002-1825-0097")
        repository = {
            "id": 123, "owner": {"id": 456, "login": "owner", "type": "User"},
            "name": "research", "full_name": "owner/research", "default_branch": "main",
            "private": False, "visibility": "public", "disabled": False, "fork": False,
        }
        upstream_calls = []
        token_exchanges = []

        def github_api(request, timeout):
            upstream_calls.append(request.full_url)
            if request.full_url == "https://api.github.com/repos/owner/research":
                payload = repository
            elif request.full_url == "https://api.github.com/user":
                self.assertEqual(request.get_header("Authorization"), "Bearer private-oauth-token")
                payload = {"id": 456, "login": "owner", "type": "User"}
            else:
                self.fail(f"Unexpected verification API endpoint: {request.full_url}")
            response = io.BytesIO(json.dumps(payload).encode())
            response.status = 200
            return response

        def allauth_http(session, method, url, **kwargs):
            response = Response()
            response.status_code = 200
            response.headers["content-type"] = "application/json"
            if url == "https://github.com/login/oauth/access_token":
                self.assertEqual(method, "POST")
                token_exchanges.append(kwargs["data"])
                payload = {"access_token": "private-oauth-token", "token_type": "bearer", "scope": ""}
            elif url == "https://api.github.com/user":
                self.assertEqual(method.lower(), "get")
                payload = {"id": 456, "login": "owner", "type": "User"}
            else:
                self.fail(f"Unexpected allauth endpoint: {url}")
            response._content = json.dumps(payload).encode()
            return response

        with patch("urllib.request.OpenerDirector.open", side_effect=github_api), patch(
            "requests.sessions.Session.request", autospec=True, side_effect=allauth_http,
        ):
            start = self.client.post(self.url("github_start"))
            self.assertEqual(start.status_code, 302)
            query = parse_qs(urlsplit(start["Location"]).query)
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertNotIn("scope", query)
            self.assertNotIn("private-oauth-token", start["Location"])
            attempt = VerificationAttempt.objects.get(submission=self.submission)
            callback_url = reverse("github_callback")
            callback_params = {"state": query["state"][0], "code": "provider-code"}
            finish = self.client.get(callback_url, callback_params)
            self.assertEqual(finish.status_code, 302)
            self.assertEqual(finish["Location"], self.url())
            proof = RepositoryVerification.objects.get(submission=self.submission)
            self.assertEqual(proof.user, self.owner)
            self.assertEqual(proof.orcid, orcid.uid)
            self.assertEqual(proof.github_user_id, 456)
            self.assertEqual(proof.role, "owner")
            self.assertEqual(proof.attempt, attempt)
            self.assertEqual(self.client.session["_auth_user_id"], str(self.owner.pk))
            self.assertFalse(SocialToken.objects.exists())
            self.assertFalse(SocialAccount.objects.filter(provider="github").exists())
            self.assertEqual(len(token_exchanges), 1)
            verifier = token_exchanges[0]["code_verifier"]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            self.assertEqual(query["code_challenge"], [challenge])
            call_count = len(upstream_calls)
            self.client.get(callback_url, callback_params)
            self.assertEqual(len(upstream_calls), call_count)
            self.assertEqual(len(token_exchanges), 1)
            self.assertEqual(RepositoryVerification.objects.filter(submission=self.submission).count(), 1)
            self.assertNotIn("private-oauth-token", json.dumps(dict(self.client.session)))
