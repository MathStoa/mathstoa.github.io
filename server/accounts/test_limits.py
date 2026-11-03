"""Admin throttles apply after CSRF without enabling password authentication."""
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings

from accounts.middleware import admin_network_key


@override_settings(ACCOUNT_RATE_LIMITS={"admin_login_network": "6/5m/key", "admin_login_account": "2/5m/key"})
class AdminLoginLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def client_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/admin/login/", follow=True)
        return client, client.cookies["csrftoken"].value

    def post(self, client, token, username="reviewer", **headers):
        return client.post("/admin/login/", {"username": username, "password": "incorrect-test-password", "csrfmiddlewaretoken": token}, **headers)

    def test_account_limit_precedes_password_check_and_is_case_normalized(self):
        client, token = self.client_and_csrf()
        with patch("django.contrib.auth.forms.authenticate", return_value=None) as authenticate:
            self.assertEqual(self.post(client, token, "Reviewer").status_code, 302)
            self.assertEqual(self.post(client, token, "reviewer").status_code, 302)
            response = self.post(client, token, " REVIEWER ")
        authenticate.assert_not_called()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "300")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotContains(response, "reviewer", status_code=429)

    def test_account_limit_shared_across_clients_and_networks(self):
        first, first_token = self.client_and_csrf()
        second, second_token = self.client_and_csrf()
        with patch("django.contrib.auth.forms.authenticate", return_value=None) as authenticate:
            self.assertEqual(self.post(first, first_token, REMOTE_ADDR="198.51.100.10").status_code, 302)
            self.assertEqual(self.post(second, second_token, REMOTE_ADDR="203.0.113.20").status_code, 302)
            self.assertEqual(self.post(second, second_token, REMOTE_ADDR="203.0.113.21").status_code, 429)
            self.assertEqual(self.post(second, second_token, username="another-reviewer", REMOTE_ADDR="203.0.113.21").status_code, 302)
        authenticate.assert_not_called()

    @override_settings(ACCOUNT_RATE_LIMITS={"admin_login_network": "2/5m/key", "admin_login_account": "100/5m/key"})
    def test_rotating_forwarded_headers_cannot_bypass_network_limit(self):
        client, token = self.client_and_csrf()
        with patch("django.contrib.auth.forms.authenticate", return_value=None) as authenticate:
            for index in range(2):
                self.assertEqual(self.post(client, token, username=f"unknown-{index}", HTTP_X_FORWARDED_FOR=f"203.0.113.{index}", HTTP_FORWARDED=f"for=203.0.113.{index}", HTTP_CF_CONNECTING_IP=f"203.0.113.{index}", REMOTE_ADDR="10.0.0.5").status_code, 302)
            response = self.post(client, token, username="third", HTTP_X_FORWARDED_FOR="203.0.113.99", REMOTE_ADDR="10.0.0.5")
        self.assertEqual(response.status_code, 429)
        authenticate.assert_not_called()

    def test_csrf_invalid_requests_do_not_consume_login_allowance(self):
        client, token = self.client_and_csrf()
        with patch("accounts.middleware.ratelimit.consume") as consume:
            response = client.post("/admin/login/", {"username": "reviewer", "password": "test"})
        self.assertEqual(response.status_code, 403)
        consume.assert_not_called()
        with patch("django.contrib.auth.forms.authenticate", return_value=None):
            self.assertEqual(self.post(client, token).status_code, 302)

    def test_get_requests_do_not_consume_allowance(self):
        with patch("accounts.middleware.ratelimit.consume") as consume:
            self.assertEqual(self.client.get("/admin/login/").status_code, 302)
        consume.assert_not_called()

    def test_network_key_canonicalizes_addresses_and_ignores_headers(self):
        from django.test import RequestFactory
        factory = RequestFactory()
        first = factory.post("/admin/login/", REMOTE_ADDR="::ffff:192.0.2.1", HTTP_X_FORWARDED_FOR="203.0.113.1")
        second = factory.post("/admin/login/", REMOTE_ADDR="192.0.2.1", HTTP_X_FORWARDED_FOR="203.0.113.2")
        self.assertEqual(admin_network_key(first), admin_network_key(second))
        first.META["REMOTE_ADDR"] = "2001:db8:abcd:1234::1"
        second.META["REMOTE_ADDR"] = "2001:db8:abcd:1234::2"
        self.assertEqual(admin_network_key(first), admin_network_key(second))


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "admin_limit_test_cache"}},
    ACCOUNT_RATE_LIMITS={"admin_login_network": "100/5m/key", "admin_login_account": "1/5m/key"},
)
class SharedDatabaseAdminLimitTests(TransactionTestCase):
    def setUp(self):
        call_command("createcachetable", verbosity=0)
        cache.clear()

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE admin_limit_test_cache")

    def test_a_new_client_reads_the_limit_from_shared_database_cache(self):
        first, second = Client(), Client()
        with patch("django.contrib.auth.forms.authenticate", return_value=None) as authenticate:
            self.assertEqual(first.post("/admin/login/", {"username": "private-reviewer", "password": "incorrect"}, REMOTE_ADDR="192.0.2.1").status_code, 302)
            self.assertEqual(second.post("/admin/login/", {"username": "private-reviewer", "password": "incorrect"}, REMOTE_ADDR="192.0.2.2").status_code, 429)
        authenticate.assert_not_called()
        with connection.cursor() as cursor:
            cursor.execute("SELECT cache_key FROM admin_limit_test_cache")
            keys = " ".join(row[0] for row in cursor.fetchall())
        self.assertNotIn("private-reviewer", keys)
        self.assertNotIn("192.0.2.1", keys)
