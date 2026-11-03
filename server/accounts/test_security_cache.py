"""Capacity and expiry races must never erase active authentication limits."""
import base64
import hashlib
import hmac
import pickle
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.core.cache import cache, caches
from django.core.cache.backends.db import DatabaseCache
from django.core.management import call_command
from django.db import DatabaseError, connection, connections
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.utils import timezone

from accounts.middleware import limit_action


def private_test_key(key, prefix, version):
    return hmac.new(b"test-only-cache-key", f"{prefix}:{version}:{key}".encode(), hashlib.sha256).hexdigest()


@override_settings(
    CACHES={"default": {
        "BACKEND": "accounts.security_cache.SecurityDatabaseCache",
        "LOCATION": "security_cache_tests", "OPTIONS": {"MAX_ENTRIES": 2, "CULL_FREQUENCY": 0},
    }},
    ACCOUNT_RATE_LIMITS={"security_cache_probe": "1/5m/key"},
)
class SecurityCacheTests(TransactionTestCase):
    def setUp(self):
        call_command("createcachetable", verbosity=0)
        cache.clear()

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE security_cache_tests")

    def insert_expired(self, count):
        expired = connection.ops.adapt_datetimefield_value(timezone.now() - timedelta(days=1))
        encoded = base64.b64encode(pickle.dumps("expired-value")).decode()
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO security_cache_tests (cache_key, value, expires) VALUES (%s, %s, %s)",
                [(cache.make_key(f"expired-{index}"), encoded, expired) for index in range(count)],
            )

    def expired_count(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM security_cache_tests WHERE expires < %s", [connection.ops.adapt_datetimefield_value(timezone.now())])
            return cursor.fetchone()[0]

    def test_capacity_pressure_preserves_all_active_values_even_with_zero_cull_frequency(self):
        cache.set("held-lock", "holder", 300)
        for index in range(20):
            cache.set(f"counter-{index}", [index], 300)
        self.assertFalse(cache.add("held-lock", "intruder", 300))
        self.assertEqual(cache.get("held-lock"), "holder")
        self.assertEqual(cache.get_many([f"counter-{index}" for index in range(20)]), {f"counter-{index}": [index] for index in range(20)})

    def test_rate_limit_remains_enforced_after_many_other_keys_are_added(self):
        request = RequestFactory().post("/admin/login/")
        self.assertIsNone(limit_action(request, action="security_cache_probe", key="protected-account", retry_after=300))
        for index in range(30):
            cache.add(f"unrelated-{index}", "live", 300)
        self.assertEqual(limit_action(request, action="security_cache_probe", key="protected-account", retry_after=300).status_code, 429)

    def test_pressure_cleanup_removes_at_most_one_expired_batch_per_write(self):
        cache.set("active", "keep", 300)
        self.insert_expired(125)
        cache.set("trigger", "new", 300)
        self.assertEqual(self.expired_count(), 25)
        self.assertEqual(cache.get("active"), "keep")
        cache.add("next-trigger", "new", 300)
        self.assertEqual(self.expired_count(), 0)
        self.assertEqual(cache.get("active"), "keep")

    def test_expired_get_does_not_delete_a_concurrently_reacquired_lock(self):
        cache.set("lock", "expired-owner", -10)
        backend = caches["default"]
        original = backend._delete_expired

        def renew_before_cleanup(conn, cursor, keys, before):
            self.assertTrue(cache.add("lock", "new-owner", 300))
            return original(conn, cursor, keys, before)

        with patch.object(backend, "_delete_expired", side_effect=renew_before_cleanup):
            self.assertIsNone(cache.get("lock"))
        self.assertEqual(cache.get("lock"), "new-owner")
        self.assertFalse(cache.add("lock", "third-owner", 300))

    def test_pressure_cleanup_rechecks_expiry_after_selecting_its_batch(self):
        cache.set("lock", "expired-owner", -10)
        cache.set("live-a", "a", 300)
        cache.set("live-b", "b", 300)
        backend = caches["default"]
        original = backend._delete_expired

        def renew_before_cleanup(conn, cursor, keys, before):
            # Simulate another connection winning the row between SELECT and DELETE.
            cursor.execute("UPDATE security_cache_tests SET expires = %s WHERE cache_key = %s", [connection.ops.adapt_datetimefield_value(timezone.now() + timedelta(minutes=5)), cache.make_key("lock")])
            return original(conn, cursor, keys, before)

        with patch.object(backend, "_delete_expired", side_effect=renew_before_cleanup):
            cache.set("trigger", "new", 300)
        self.assertEqual(cache.get("lock"), "expired-owner")

    def competing_adds(self, key):
        start = Barrier(4)

        def compete(index):
            try:
                start.wait(timeout=5)
                return index, cache.add(key, index, 300)
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(compete, range(4)))
        winners = [index for index, success in results if success]
        self.assertLessEqual(len(winners), 1, results)
        if winners:
            self.assertEqual(cache.get(key), winners[0])
        else:
            # SQLite's table-wide write locks may make all simultaneous attempts
            # fail closed. allauth retries add(); a later attempt must succeed.
            self.assertEqual(connection.vendor, "sqlite", results)
            self.assertTrue(cache.add(key, "retry-winner", 300))
            self.assertEqual(cache.get(key), "retry-winner")

    def test_concurrent_add_of_missing_key_never_grants_two_locks(self):
        for attempt in range(3):
            self.competing_adds(f"new-lock-{attempt}")

    def test_concurrent_add_of_expired_key_never_grants_two_locks(self):
        for attempt in range(3):
            cache.set(f"old-lock-{attempt}", "expired-owner", -10)
            self.competing_adds(f"old-lock-{attempt}")

    def test_custom_private_key_function_and_versions_are_preserved(self):
        configured = {"default": {
            "BACKEND": "accounts.security_cache.SecurityDatabaseCache", "LOCATION": "security_cache_tests",
            "KEY_PREFIX": "private-prefix", "KEY_FUNCTION": "accounts.test_security_cache.private_test_key",
            "OPTIONS": {"MAX_ENTRIES": 2},
        }}
        with override_settings(CACHES=configured):
            cache.set("private-account", "value-one", 300, version=1)
            self.assertTrue(cache.add("private-account", "value-two", 300, version=2))
            self.assertFalse(cache.add("private-account", "replacement", 300, version=1))
            self.assertEqual(cache.get("private-account", version=1), "value-one")
            self.assertEqual(cache.get("private-account", version=2), "value-two")
            with connection.cursor() as cursor:
                cursor.execute("SELECT cache_key FROM security_cache_tests")
                keys = {row[0] for row in cursor.fetchall()}
            self.assertEqual(keys, {private_test_key("private-account", "private-prefix", version) for version in (1, 2)})

    def test_failed_counter_write_raises_generic_error_instead_of_allowing_access(self):
        with patch.object(DatabaseCache, "_base_set", return_value=False), self.assertNoLogs("accounts.security_cache"):
            with self.assertRaisesMessage(DatabaseError, "Security counter storage is unavailable."):
                cache.set("private-counter-key", "private-counter-value", 300)

    def test_failed_lock_write_is_false_and_does_not_log_key_or_value(self):
        with patch.object(connection, "cursor", side_effect=DatabaseError("private-error-detail")), self.assertNoLogs("accounts.security_cache"):
            self.assertFalse(cache.add("private-lock-key", "private-lock-value", 300))
