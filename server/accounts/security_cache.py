"""Database cache for security state: pressure never evicts live counters.

MAX_ENTRIES is a cleanup trigger, not a hard cap. Only expired rows may be
removed automatically. Operators must monitor database size and run periodic
expired-row cleanup; accepting more live counters trades storage availability
for preserving authentication limits. Explicit administrator clear() is still
destructive and must not be used as routine maintenance.

The table/schema and KEY_FUNCTION remain compatible with Django DatabaseCache.
The backend supports PostgreSQL and SQLite. Consumers needing a read/modify/write
operation must use an add()-based lock, as allauth does; incr() is not atomic.
"""
import base64
import pickle
from datetime import datetime, timezone

from django.conf import settings
from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.db import DatabaseCache
from django.db import DatabaseError, connections, models, router, transaction
from django.utils.timezone import now as timezone_now
from django.views.decorators.debug import sensitive_variables


class SecurityDatabaseCache(DatabaseCache):
    expired_cull_batch_size = 100

    @sensitive_variables()
    def _delete_expired(self, connection, cursor, keys, before):
        if not keys:
            return
        quote = connection.ops.quote_name
        # Recheck expiry in the DELETE itself: a concurrent lock acquisition or
        # counter refresh after our SELECT must never be removed by cleanup.
        cursor.execute(
            f"DELETE FROM {quote(self._table)} WHERE {quote('cache_key')} IN "
            f"({', '.join(['%s'] * len(keys))}) AND {quote('expires')} < %s",
            [*keys, connection.ops.adapt_datetimefield_value(before)],
        )

    def _cull(self, db, cursor, now, num):
        connection = connections[db]
        quote = connection.ops.quote_name
        cursor.execute(
            f"SELECT {quote('cache_key')} FROM {quote(self._table)} "
            f"WHERE {quote('expires')} < %s ORDER BY {quote('expires')} LIMIT %s",
            [connection.ops.adapt_datetimefield_value(now), self.expired_cull_batch_size],
        )
        self._delete_expired(connection, cursor, [row[0] for row in cursor.fetchall()], now)

    @sensitive_variables()
    def _base_set(self, mode, key, value, timeout=DEFAULT_TIMEOUT):
        if mode != "add":
            stored = super()._base_set(mode, key, value, timeout)
            if mode == "set" and not stored:
                # allauth does not inspect cache.set()'s return value. A failed
                # security-counter write must abort, rather than allow access.
                raise DatabaseError("Security counter storage is unavailable.")
            return stored

        db = router.db_for_write(self.cache_model_class)
        connection = connections[db]
        quote = connection.ops.quote_name
        table = quote(self._table)
        now = timezone_now().replace(microsecond=0)
        deadline = self.get_backend_timeout(timeout)
        expiry = datetime.max if deadline is None else datetime.fromtimestamp(deadline, tz=timezone.utc if settings.USE_TZ else None)
        expiry = connection.ops.adapt_datetimefield_value(expiry.replace(microsecond=0))
        encoded = base64.b64encode(pickle.dumps(value, self.pickle_protocol)).decode("latin1")
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                if count > self._max_entries:
                    self._cull(db, cursor, now, count)
                with transaction.atomic(using=db):
                    # Django's ordinary add() may read the same expired row in
                    # two transactions and let both UPDATE it successfully.
                    # Deleting only an expired row and INSERTing under one
                    # transaction makes the primary key arbitrate one winner.
                    cursor.execute(
                        f"DELETE FROM {table} WHERE {quote('cache_key')} = %s AND {quote('expires')} < %s",
                        [key, connection.ops.adapt_datetimefield_value(now)],
                    )
                    cursor.execute(
                        f"INSERT INTO {table} ({quote('cache_key')}, {quote('value')}, {quote('expires')}) VALUES (%s, %s, %s)",
                        [key, encoded, expiry],
                    )
        except DatabaseError:
            # A collision or unavailable store cannot grant an add()-based lock.
            # No key, stored value, credential or database error is logged here.
            return False
        return True

    @sensitive_variables()
    def get_many(self, keys, version=None):
        if not keys:
            return {}
        key_map = {self.make_and_validate_key(key, version=version): key for key in keys}
        db = router.db_for_read(self.cache_model_class)
        connection = connections[db]
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {quote('cache_key')}, {quote('value')}, {quote('expires')} FROM {quote(self._table)} "
                f"WHERE {quote('cache_key')} IN ({', '.join(['%s'] * len(key_map))})",
                list(key_map),
            )
            rows = cursor.fetchall()
        expression = models.Expression(output_field=models.DateTimeField())
        converters = connection.ops.get_db_converters(expression) + expression.get_db_converters(connection)
        now = timezone_now()
        result, expired = {}, []
        for key, value, expiry in rows:
            for converter in converters:
                expiry = converter(expiry, expression, connection)
            if expiry < now:
                expired.append(key)
            else:
                result[key_map[key]] = pickle.loads(base64.b64decode(connection.ops.process_clob(value).encode()))
        if expired:
            write_db = router.db_for_write(self.cache_model_class)
            write_connection = connections[write_db]
            with write_connection.cursor() as cursor:
                self._delete_expired(write_connection, cursor, expired, now)
        return result
