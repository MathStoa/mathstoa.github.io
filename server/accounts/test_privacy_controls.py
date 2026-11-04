from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.cache import private_cache_key
from accounts.maintenance import purge_transient_data
from accounts.policy import registration_is_open
from repository_verification.models import RepositoryVerification, VerificationAttempt
from submissions.models import Submission, SubmissionRevision


User = get_user_model()


class RegistrationControlsTests(TestCase):
    def setUp(self):
        cache.clear()

    def github_callback(self, uid):
        response = self.client.post("/accounts/github/login/")
        self.assertEqual(response.status_code, 302)
        state = parse_qs(urlsplit(response.url).query)["state"][0]
        profile = Mock(status_code=200)
        profile.json.return_value = {"id": uid, "login": "author"}
        remote = Mock()
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=False)
        remote.get.return_value = profile
        with patch("allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token", return_value={"access_token": "temporary"}), patch("allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_requests_session", return_value=remote):
            return self.client.get("/accounts/github/login/callback/", {"code": "test", "state": state})

    def test_registration_requires_flag_and_production_contact(self):
        for debug, enabled, contact, expected in (
            (False, False, "privacy@example.org", False),
            (False, True, "", False),
            (False, True, "   ", False),
            (False, True, "privacy@example.org", True),
            (True, True, "", True),
            (True, False, "privacy@example.org", False),
        ):
            with self.subTest(debug=debug, enabled=enabled, contact=contact), override_settings(DEBUG=debug, PUBLIC_REGISTRATION_ENABLED=enabled, PUBLIC_CONTACT_EMAIL=contact):
                self.assertEqual(registration_is_open(), expected)

    def test_closed_registration_blocks_new_email_and_provider_accounts(self):
        for enabled, contact in ((False, "privacy@example.org"), (True, "")):
            with self.subTest(enabled=enabled), override_settings(DEBUG=False, PUBLIC_REGISTRATION_ENABLED=enabled, PUBLIC_CONTACT_EMAIL=contact):
                response = self.client.post("/accounts/signup/", {"email": "new@example.org"})
                self.assertContains(response, "New account registration is currently closed")
                response = self.github_callback(98765)
                self.assertContains(response, "New account registration is currently closed")
                self.assertFalse(User.objects.exists())
                self.assertNotIn("_auth_user_id", self.client.session)
                self.client.logout()

    def test_existing_provider_login_survives_closed_registration_and_missing_contact(self):
        user = User.objects.create_user(username="existing-provider")
        SocialAccount.objects.create(user=user, provider="github", uid="12345")
        for enabled, contact in ((False, "privacy@example.org"), (True, "")):
            with self.subTest(enabled=enabled), override_settings(DEBUG=False, PUBLIC_REGISTRATION_ENABLED=enabled, PUBLIC_CONTACT_EMAIL=contact):
                response = self.github_callback(12345)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
                self.assertEqual(User.objects.count(), 1)
                self.client.logout()

    @override_settings(DEBUG=False, PUBLIC_REGISTRATION_ENABLED=False, PUBLIC_CONTACT_EMAIL="")
    def test_existing_email_login_remains_available_without_signup_links(self):
        user = User.objects.create_user(username="existing-email", email="author@example.org")
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
        response = self.client.get("/accounts/login/")
        self.assertContains(response, "New account registration is currently closed")
        self.assertContains(response, "Continue with GitHub")
        self.assertContains(response, "Email me a sign-in code")
        self.assertNotContains(response, "Create an account with email")
        self.assertNotContains(response, "Your first sign-in creates")
        response = self.client.get("/accounts/login/code/")
        self.assertNotContains(response, 'href="/accounts/signup/"')
        with patch("allauth.account.adapter.DefaultAccountAdapter.generate_login_code", return_value="123456"):
            self.client.post("/accounts/login/code/", {"email": user.email})
        self.client.post("/accounts/login/code/confirm/", {"code": "123456"})
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)


class PrivacyNoticeTests(TestCase):
    def test_notice_states_cookies_hosting_repository_checks_and_expiry(self):
        response = self.client.get("/privacy/")
        self.assertEqual(response.status_code, 200)
        for sentence in (
            "an internal account name generated at random",
            "where offered, an institutional sign-in",
            "only those needed to run the service",
            'There is no "remember this browser" cookie',
            "Sign-in sessions last up to 14 days",
            "a completed authorization is valid for 30 days",
            "Render hosts the website and database in Frankfurt on the controllers' instructions, with Cloudflare's network in front",
            "The website's own request log keeps only the time, method, status and size of each request",
            "the website queries GitHub about that public repository",
            'id="retention"',
        ):
            self.assertContains(response, sentence)

    @override_settings(PUBLIC_CONTACT_EMAIL="privacy@example.invalid")
    def test_notice_gives_controllers_bases_transfers_rights_and_complaint_route(self):
        response = self.client.get("/privacy/")
        for sentence in (
            "Last updated",
            'id="summary"',
            "is run by Tommaso Rosati and Simon Gabriel in a personal capacity",
            "They are joint controllers",
            'id="named"',
            "in performance of the controllers' contract with you",
            "in the controllers' legitimate interest",
            "to meet the controllers' legal obligations",
            "Render is a United States company",
            "Data Privacy Framework and its UK Extension",
            "restrict its use",
            "machine-readable format",
            'id="object"',
            "object at any time",
            "https://ico.org.uk/make-a-complaint/",
            "Anyone, with or without an account",
        ):
            self.assertContains(response, sentence)
        self.assertContains(response, "mailto:privacy@example.invalid", count=2)

    def test_email_collection_wording_follows_email_login_setting(self):
        offered = "If you sign in with email or add an email address"
        not_offered = "do not retain an email address from your sign-in provider for account access;"
        with override_settings(EMAIL_LOGIN_ENABLED=False):
            response = self.client.get("/privacy/")
            self.assertContains(response, not_offered)
            self.assertNotContains(response, offered)
            self.assertNotContains(response, "Unverified email registrations")
        with override_settings(EMAIL_LOGIN_ENABLED=True):
            response = self.client.get("/privacy/")
            self.assertContains(response, offered)
            self.assertContains(response, "Unverified email registrations are deleted after seven days")
            self.assertNotContains(response, not_offered)


class PrivateCacheKeyTests(SimpleTestCase):
    def test_key_hides_identifiers_and_preserves_namespace_boundaries(self):
        raw = "allauth:rl:login:ip:198.51.100.7:author@example.org"
        result = private_cache_key(raw, "sensitive-prefix", 1)
        self.assertEqual(result, private_cache_key(raw, "sensitive-prefix", 1))
        self.assertRegex(result, r"^mathstoa:[0-9a-f]{64}$")
        self.assertNotIn("198.51.100.7", result)
        self.assertNotIn("author", result)
        self.assertNotIn("sensitive-prefix", result)
        for key, prefix, version in ((raw + "x", "sensitive-prefix", 1), (raw, "other", 1), (raw, "sensitive-prefix", 2)):
            self.assertNotEqual(result, private_cache_key(key, prefix, version))
        self.assertNotEqual(private_cache_key("c", "a:b", 1), private_cache_key("b:c", "a", 1))

    def test_secret_key_changes_digest(self):
        with override_settings(SECRET_KEY="first-strong-secret-" + "x" * 64):
            first = private_cache_key("198.51.100.7", "", 1)
        with override_settings(SECRET_KEY="different-strong-secret-" + "y" * 64):
            self.assertNotEqual(first, private_cache_key("198.51.100.7", "", 1))


@override_settings(CACHES={"default": {
    "BACKEND": "django.core.cache.backends.db.DatabaseCache",
    "LOCATION": "privacy_cleanup_test_cache",
    "KEY_FUNCTION": "accounts.cache.private_cache_key",
}})
class TransientDataCleanupTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("createcachetable", "privacy_cleanup_test_cache", verbosity=0)

    @classmethod
    def tearDownClass(cls):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE " + connection.ops.quote_name("privacy_cleanup_test_cache"))
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.owner = User.objects.create_user(username="owner")
        self.submission = Submission.objects.create(owner=self.owner, title="Preserved project", repository_url="https://github.com/example/project")
        self.revision = SubmissionRevision.objects.create(submission=self.submission, number=1, snapshot=self.submission.snapshot(), status="approved", submitted_by=self.owner)
        self.submission.published_revision = self.revision
        self.submission.save(update_fields=["published_revision"])
        self.expired_session = Session.objects.create(session_key="expired", session_data="", expire_date=self.now - timedelta(days=1))
        self.active_session = Session.objects.create(session_key="active", session_data="", expire_date=self.now + timedelta(days=1))
        cache.set("old-ip:198.51.100.7", 1, timeout=-60)
        cache.set("active-email:author@example.org", 1, timeout=3600)
        self.old_attempt = self.attempt("d", days_old=40)
        self.recent_attempt = self.attempt("e", days_old=1)
        self.proven_attempt = self.attempt("f", days_old=40)
        self.proof = RepositoryVerification.objects.create(
            attempt=self.proven_attempt, submission=self.submission, user=self.owner,
            method="file", repository_url=self.submission.repository_url,
            repository_id=42, repository_owner_id=43, full_name="example/project",
            commit_sha="0" * 40, valid_until=self.now - timedelta(days=1),
        )

    def attempt(self, token, *, days_old):
        attempt = VerificationAttempt.objects.create(
            submission=self.submission, user=self.owner, method="file",
            repository_url=self.submission.repository_url, repository_id=42,
            repository_owner_id=43, full_name="example/project", token_hash=token * 64,
            expires_at=self.now - timedelta(hours=1),
        )
        VerificationAttempt.objects.filter(pk=attempt.pk).update(created_at=self.now - timedelta(days=days_old))
        return attempt

    def cache_keys(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT cache_key FROM " + connection.ops.quote_name("privacy_cleanup_test_cache"))
            return [row[0] for row in cursor.fetchall()]

    def test_database_cache_stores_only_keyed_identifiers(self):
        keys = self.cache_keys()
        self.assertEqual(len(keys), 2)
        for key in keys:
            self.assertRegex(key, r"^mathstoa:[0-9a-f]{64}$")
        self.assertEqual(cache.get("active-email:author@example.org"), 1)

    def test_dry_run_counts_without_deleting_then_cleanup_keeps_content_and_proof(self):
        output = StringIO()
        call_command("purge_transient_data", dry_run=True, stdout=output)
        self.assertIn("Would remove: sessions=1, cache_entries=1, verification_attempts=1,", output.getvalue())
        self.assertEqual(Session.objects.count(), 2)
        self.assertEqual(len(self.cache_keys()), 2)
        self.assertEqual(VerificationAttempt.objects.count(), 3)
        counts = purge_transient_data()
        retained = {"unverified_accounts": 0, "inactive_accounts": 0, "unused_drafts": 0, "draft_verifications": 0}
        self.assertEqual(counts, {"sessions": 1, "cache_entries": 1, "verification_attempts": 1, **retained})
        self.assertFalse(Session.objects.filter(pk=self.expired_session.pk).exists())
        self.assertTrue(Session.objects.filter(pk=self.active_session.pk).exists())
        self.assertEqual(len(self.cache_keys()), 1)
        self.assertEqual(cache.get("active-email:author@example.org"), 1)
        self.assertFalse(VerificationAttempt.objects.filter(pk=self.old_attempt.pk).exists())
        self.assertEqual(VerificationAttempt.objects.count(), 2)
        self.assertTrue(RepositoryVerification.objects.filter(pk=self.proof.pk).exists())
        self.assertTrue(Submission.objects.filter(pk=self.submission.pk, published_revision=self.revision).exists())
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(purge_transient_data(), {"sessions": 0, "cache_entries": 0, "verification_attempts": 0, **retained})

    def test_invalid_retention_does_not_remove_anything(self):
        with self.assertRaises(CommandError):
            call_command("purge_transient_data", days=0, stdout=StringIO())
        self.assertEqual(Session.objects.count(), 2)
        self.assertEqual(len(self.cache_keys()), 2)
