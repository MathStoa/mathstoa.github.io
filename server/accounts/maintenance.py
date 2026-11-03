"""Operational expiry and approved retention; submitted records are untouched."""
from contextlib import contextmanager
from io import StringIO

from django.contrib.sessions.models import Session
from django.core.cache import caches
from django.core.cache.backends.db import DatabaseCache
from django.core.management import call_command
from django.db import connections, router
from django.utils import timezone

from repository_verification.management.commands.purge_repository_verification_attempts import Command as VerificationCleanup
from .retention import purge_retained_data


@contextmanager
def transient_cleanup_lock():
    """PostgreSQL coordinates instances and releases the lock on process death."""
    default_connection = connections["default"]
    if default_connection.vendor != "postgresql":
        # SQLite is only supported for local, single-instance development.
        yield True
        return
    # Keep the lock separate from connections used or closed by cleanup helpers.
    connection = default_connection.copy(alias="mathstoa_transient_cleanup_lock")
    lock_id = 0x4D53544F41504301  # Stable namespace dedicated to mathStoa cleanup.
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            acquired = cursor.fetchone()[0]
        try:
            yield acquired
        finally:
            if acquired:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
    finally:
        connection.close()


def purge_transient_data(*, dry_run=False, days=30):
    if days < 1:
        raise ValueError("days must be at least 1")
    with transient_cleanup_lock() as acquired:
        if not acquired:
            return None
        return _purge_transient_data(dry_run=dry_run, days=days)


def _purge_transient_data(*, dry_run, days):
    now = timezone.now()
    sessions = Session.objects.filter(expire_date__lt=now)
    counts = {"sessions": sessions.count(), "cache_entries": 0}
    if not dry_run:
        counts["sessions"], _ = sessions.delete()

    cache = caches["default"]
    if isinstance(cache, DatabaseCache):
        connection = connections[router.db_for_write(cache.cache_model_class)]
        quote = connection.ops.quote_name
        table = quote(cache.cache_model_class._meta.db_table)
        expires = quote("expires")
        cutoff = connection.ops.adapt_datetimefield_value(now)
        with connection.cursor() as cursor:
            if dry_run:
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {expires} < %s", [cutoff])
                counts["cache_entries"] = cursor.fetchone()[0]
            else:
                cursor.execute(f"DELETE FROM {table} WHERE {expires} < %s", [cutoff])
                counts["cache_entries"] = cursor.rowcount

    # Inspect account responsibilities and preserve draft activity before the
    # short-lived supporting verification attempt records are removed.
    retention_counts = purge_retained_data(now=now, dry_run=dry_run)
    command = VerificationCleanup()
    call_command(command, days=days, dry_run=dry_run, stdout=StringIO())
    counts["verification_attempts"] = command.record_count
    counts.update(retention_counts)
    return counts
