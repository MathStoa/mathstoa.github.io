"""Check production startup settings without connecting to an external service."""
import os
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

from django.core.exceptions import DisallowedHost, ImproperlyConfigured
from django.test import RequestFactory, SimpleTestCase, override_settings


class RenderSettingsTests(SimpleTestCase):
    def load_settings(self, **environment):
        values = {
            "DJANGO_DEBUG": "0",
            "DJANGO_SECRET_KEY": "settings-test-only-" + "x" * 64,
            "DATABASE_URL": "postgresql://test:test@localhost/mathstoa",
            **environment,
        }
        with patch.dict(os.environ, values, clear=True), patch.object(sys, "path", sys.path.copy()):
            return runpy.run_path(str(Path(__file__).resolve().parents[1] / "config" / "settings.py"))

    def test_first_render_deploy_uses_assigned_host_and_rejects_other_tenants(self):
        configured = self.load_settings(
            RENDER_EXTERNAL_HOSTNAME="math-stoa-abc123.onrender.com",
            RENDER_EXTERNAL_URL="https://math-stoa-abc123.onrender.com",
        )
        self.assertEqual(configured["PUBLIC_BASE_URL"], "https://math-stoa-abc123.onrender.com")
        self.assertEqual(configured["ALLOWED_HOSTS"], ["math-stoa-abc123.onrender.com"])
        self.assertEqual(configured["CSRF_TRUSTED_ORIGINS"], ["https://math-stoa-abc123.onrender.com"])
        with override_settings(ALLOWED_HOSTS=configured["ALLOWED_HOSTS"]):
            request = RequestFactory().get("/health/", HTTP_HOST="math-stoa-abc123.onrender.com")
            self.assertEqual(request.get_host(), "math-stoa-abc123.onrender.com")
            with self.assertRaises(DisallowedHost):
                RequestFactory().get("/health/", HTTP_HOST="another-tenant.onrender.com").get_host()

    def test_plausible_script_id_is_optional_and_cannot_change_script_origin(self):
        self.assertEqual(self.load_settings(DJANGO_ALLOWED_HOSTS="math-stoa.org")["PLAUSIBLE_SCRIPT_ID"], "")
        self.assertEqual(self.load_settings(DJANGO_ALLOWED_HOSTS="math-stoa.org", PLAUSIBLE_SCRIPT_ID="pa-example_123")["PLAUSIBLE_SCRIPT_ID"], "pa-example_123")
        for value in ("https://evil.example/track.js", "pa-example.js", "pa-example/../../script", "pa-example?query=1", 'pa-\" onload=\"bad', "pa-", "pa-" + "x" * 129):
            with self.subTest(value=value), self.assertRaisesMessage(ImproperlyConfigured, "PLAUSIBLE_SCRIPT_ID"):
                self.load_settings(DJANGO_ALLOWED_HOSTS="math-stoa.org", PLAUSIBLE_SCRIPT_ID=value)

    def test_custom_canonical_url_and_explicit_origins_are_preserved(self):
        configured = self.load_settings(
            RENDER_EXTERNAL_HOSTNAME="math-stoa.onrender.com",
            RENDER_EXTERNAL_URL="https://math-stoa.onrender.com/",
            DJANGO_ALLOWED_HOSTS="math-stoa.org,math-stoa.onrender.com",
            PUBLIC_BASE_URL="https://math-stoa.org",
            DJANGO_CSRF_TRUSTED_ORIGINS="https://math-stoa.org,https://preview.math-stoa.org",
        )
        self.assertEqual(configured["PUBLIC_BASE_URL"], "https://math-stoa.org")
        self.assertEqual(configured["ALLOWED_HOSTS"], ["math-stoa.org", "math-stoa.onrender.com"])
        self.assertEqual(configured["CSRF_TRUSTED_ORIGINS"], [
            "https://math-stoa.org", "https://preview.math-stoa.org", "https://math-stoa.onrender.com",
        ])

    def test_render_hostname_alone_supplies_https_origin(self):
        configured = self.load_settings(RENDER_EXTERNAL_HOSTNAME="math-stoa.onrender.com")
        self.assertEqual(configured["PUBLIC_BASE_URL"], "https://math-stoa.onrender.com")

    def test_invalid_render_hostnames_fail_startup(self):
        for hostname in (
            "*", ".onrender.com", "*.onrender.com", "onrender.com",
            "math-stoa.onrender.com.evil.example", "https://math-stoa.onrender.com",
            "math-stoa.onrender.com:443", "math-stoa.onrender.com/path",
            "user@math-stoa.onrender.com", "math-stoa.onrender.com\n",
            "sub.math-stoa.onrender.com", "-math-stoa.onrender.com",
        ):
            with self.subTest(hostname=hostname), self.assertRaisesMessage(ImproperlyConfigured, "RENDER_EXTERNAL_HOSTNAME"):
                self.load_settings(RENDER_EXTERNAL_HOSTNAME=hostname)

    def test_invalid_or_mismatched_render_urls_fail_startup(self):
        for external_url in (
            "http://math-stoa.onrender.com", "https://another.onrender.com",
            "https://math-stoa.onrender.com/path", "https://math-stoa.onrender.com?next=evil",
            "https://math-stoa.onrender.com#fragment", "https://user@math-stoa.onrender.com",
            "https://math-stoa.onrender.com:443", "https://math-stoa.onrender.com//",
        ):
            with self.subTest(external_url=external_url), self.assertRaisesMessage(ImproperlyConfigured, "RENDER_EXTERNAL_URL"):
                self.load_settings(RENDER_EXTERNAL_HOSTNAME="math-stoa.onrender.com", RENDER_EXTERNAL_URL=external_url)
        with self.assertRaisesMessage(ImproperlyConfigured, "RENDER_EXTERNAL_URL"):
            self.load_settings(RENDER_EXTERNAL_URL="https://math-stoa.onrender.com")

    def test_non_render_defaults_remain_unchanged(self):
        development = self.load_settings(DJANGO_DEBUG="1")
        self.assertEqual(development["PUBLIC_BASE_URL"], "http://localhost:8000")
        self.assertEqual(development["ALLOWED_HOSTS"], ["localhost", "127.0.0.1"])
        self.assertEqual(development["CSRF_TRUSTED_ORIGINS"], ["http://localhost:8000"])
        self.assertTrue(development["PUBLIC_REGISTRATION_ENABLED"])
        production = self.load_settings(DJANGO_ALLOWED_HOSTS="math-stoa.org")
        self.assertEqual(production["PUBLIC_BASE_URL"], "https://math-stoa.org")
        self.assertEqual(production["ALLOWED_HOSTS"], ["math-stoa.org"])
        self.assertFalse(production["PUBLIC_REGISTRATION_ENABLED"])
        with self.assertRaises(ImproperlyConfigured):
            self.load_settings()

    def test_previous_encryption_keys_must_be_a_list_of_strong_keys(self):
        configured = self.load_settings(
            DJANGO_ALLOWED_HOSTS="math-stoa.org",
            DJANGO_SECRET_KEY_FALLBACKS='["previous-test-key-' + "x" * 64 + '"]',
        )
        self.assertEqual(len(configured["SECRET_KEY_FALLBACKS"]), 1)
        for value in ('"not-a-list"', '["short"]', '[null]', '{"key":"value"}', 'malformed'):
            with self.subTest(value=value), self.assertRaises(ImproperlyConfigured):
                self.load_settings(DJANGO_ALLOWED_HOSTS="math-stoa.org", DJANGO_SECRET_KEY_FALLBACKS=value)

    def test_production_email_signin_rejects_unencrypted_smtp(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "SMTP encryption"):
            self.load_settings(
                DJANGO_ALLOWED_HOSTS="math-stoa.org", EMAIL_LOGIN_ENABLED="true",
                EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="signin@example.org",
                EMAIL_USE_TLS="false", EMAIL_USE_SSL="false",
            )

    def test_production_email_signin_accepts_each_encrypted_smtp_mode(self):
        for use_tls, use_ssl, port in (("true", "false", "587"), ("false", "true", "465")):
            with self.subTest(use_tls=use_tls, use_ssl=use_ssl):
                configured = self.load_settings(
                    DJANGO_ALLOWED_HOSTS="math-stoa.org", EMAIL_LOGIN_ENABLED="true",
                    EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="signin@example.org",
                    EMAIL_USE_TLS=use_tls, EMAIL_USE_SSL=use_ssl, EMAIL_PORT=port,
                )
                self.assertTrue(configured["EMAIL_LOGIN_ENABLED"])
                self.assertEqual(configured["EMAIL_USE_TLS"], use_tls == "true")
                self.assertEqual(configured["EMAIL_USE_SSL"], use_ssl == "true")

    def test_production_email_signin_still_rejects_conflicting_smtp_modes(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "not both"):
            self.load_settings(
                DJANGO_ALLOWED_HOSTS="math-stoa.org", EMAIL_LOGIN_ENABLED="true",
                EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="signin@example.org",
                EMAIL_USE_TLS="true", EMAIL_USE_SSL="true",
            )

    def test_development_console_email_does_not_require_smtp_encryption(self):
        configured = self.load_settings(
            DJANGO_DEBUG="1", EMAIL_LOGIN_ENABLED="true",
            EMAIL_USE_TLS="false", EMAIL_USE_SSL="false",
        )
        self.assertEqual(configured["EMAIL_BACKEND"], "django.core.mail.backends.console.EmailBackend")
        self.assertTrue(configured["EMAIL_LOGIN_ENABLED"])

    def test_disabled_production_email_does_not_require_smtp_configuration(self):
        configured = self.load_settings(
            DJANGO_ALLOWED_HOSTS="math-stoa.org", EMAIL_LOGIN_ENABLED="false",
            EMAIL_USE_TLS="false", EMAIL_USE_SSL="false",
        )
        self.assertFalse(configured["EMAIL_LOGIN_ENABLED"])
        self.assertEqual(configured["EMAIL_HOST"], "")

    def test_notification_only_production_smtp_requires_encryption(self):
        for mailbox in ("", "moderators@example.org"):
            with self.subTest(mailbox=mailbox), self.assertRaisesMessage(ImproperlyConfigured, "SMTP encryption"):
                self.load_settings(
                    DJANGO_ALLOWED_HOSTS="math-stoa.org", EMAIL_LOGIN_ENABLED="false",
                    EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="notices@example.org",
                    MODERATION_NOTIFY_EMAIL=mailbox, EMAIL_USE_TLS="false", EMAIL_USE_SSL="false",
                )

    def test_notification_only_production_accepts_tls_or_ssl(self):
        for use_tls, use_ssl, port in (("true", "false", "587"), ("false", "true", "465")):
            with self.subTest(use_tls=use_tls, use_ssl=use_ssl):
                configured = self.load_settings(
                    DJANGO_ALLOWED_HOSTS="math-stoa.org", EMAIL_LOGIN_ENABLED="false",
                    EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="notices@example.org",
                    EMAIL_USE_TLS=use_tls, EMAIL_USE_SSL=use_ssl, EMAIL_PORT=port,
                )
                self.assertFalse(configured["EMAIL_LOGIN_ENABLED"])
                self.assertNotEqual(configured["EMAIL_USE_TLS"], configured["EMAIL_USE_SSL"])

    def test_notification_only_production_rejects_conflicting_tls_and_ssl(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "not both"):
            self.load_settings(
                DJANGO_ALLOWED_HOSTS="math-stoa.org", EMAIL_LOGIN_ENABLED="false",
                EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="notices@example.org",
                EMAIL_USE_TLS="true", EMAIL_USE_SSL="true",
            )

    def test_organisation_only_email_codes_enable_shared_engine_without_personal_email(self):
        configured = self.load_settings(
            DJANGO_ALLOWED_HOSTS="math-stoa.org", EMAIL_LOGIN_ENABLED="false",
            ORGANISATION_LOGIN_ENABLED="true", EMAIL_HOST="smtp.example.org",
            DEFAULT_FROM_EMAIL="signin@example.org", EMAIL_USE_TLS="true",
        )
        self.assertFalse(configured["EMAIL_LOGIN_ENABLED"])
        self.assertTrue(configured["ORGANISATION_LOGIN_ENABLED"])
        self.assertTrue(configured["ACCOUNT_LOGIN_BY_CODE_ENABLED"])

    def test_organisation_email_codes_default_to_personal_email_setting(self):
        enabled = self.load_settings(DJANGO_DEBUG="1", EMAIL_LOGIN_ENABLED="true")
        disabled = self.load_settings(DJANGO_DEBUG="1", EMAIL_LOGIN_ENABLED="false")
        self.assertTrue(enabled["ORGANISATION_LOGIN_ENABLED"])
        self.assertFalse(disabled["ORGANISATION_LOGIN_ENABLED"])
        self.assertFalse(disabled["ACCOUNT_LOGIN_BY_CODE_ENABLED"])

    def test_organisation_only_production_requires_sender_and_encrypted_smtp(self):
        base = {"DJANGO_ALLOWED_HOSTS": "math-stoa.org", "EMAIL_LOGIN_ENABLED": "false", "ORGANISATION_LOGIN_ENABLED": "true"}
        with self.assertRaisesMessage(ImproperlyConfigured, "EMAIL_HOST"):
            self.load_settings(**base)
        with self.assertRaisesMessage(ImproperlyConfigured, "SMTP encryption"):
            self.load_settings(**base, EMAIL_HOST="smtp.example.org", DEFAULT_FROM_EMAIL="signin@example.org", EMAIL_USE_TLS="false", EMAIL_USE_SSL="false")
