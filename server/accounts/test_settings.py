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
