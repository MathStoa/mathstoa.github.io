import os
from datetime import timedelta
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from unittest.mock import Mock, patch

from django.contrib.sessions.models import Session
from django.db import connection, connections
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from accounts.maintenance import purge_transient_data, transient_cleanup_lock


class CleanupLockTests(SimpleTestCase):
    def postgres(self, acquired):
        connection = Mock(vendor="postgresql")
        lock_connection = connection.copy.return_value
        cursor = Mock()
        cursor.fetchone.return_value = [acquired]
        lock_connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
        lock_connection.cursor.return_value.__exit__ = Mock(return_value=False)
        return connection, cursor

    def test_lock_is_released_even_when_cleanup_fails(self):
        connection, cursor = self.postgres(True)
        with patch("accounts.maintenance.connections", {"default": connection}):
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                with transient_cleanup_lock() as acquired:
                    self.assertTrue(acquired)
                    raise RuntimeError("test failure")
        queries = cursor.execute.call_args_list
        self.assertEqual(queries[0].args[0], "SELECT pg_try_advisory_lock(%s)")
        self.assertEqual(queries[1].args[0], "SELECT pg_advisory_unlock(%s)")
        self.assertEqual(queries[0].args[1], queries[1].args[1])
        connection.copy.assert_called_once_with(alias="mathstoa_transient_cleanup_lock")
        connection.copy.return_value.close.assert_called_once()

    def test_busy_lock_skips_cleanup_without_unlocking_another_run(self):
        connection, cursor = self.postgres(False)
        with patch("accounts.maintenance.connections", {"default": connection}), patch("accounts.maintenance._purge_transient_data") as purge:
            self.assertIsNone(purge_transient_data())
        purge.assert_not_called()
        cursor.execute.assert_called_once()
        connection.copy.return_value.close.assert_called_once()


class PostgreSQLCleanupLockTests(TransactionTestCase):
    def test_dedicated_unregistered_connection_contends_and_releases_for_real_cleanup(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL advisory locks; exercised by PostgreSQL CI")
        alias = "mathstoa_transient_cleanup_lock"
        self.assertNotIn(alias, connections)
        expired = Session.objects.create(
            session_key="postgres-expired", session_data="",
            expire_date=timezone.now() - timedelta(days=1),
        )
        active = Session.objects.create(
            session_key="postgres-active", session_data="",
            expire_date=timezone.now() + timedelta(days=1),
        )
        with self.assertRaisesRegex(RuntimeError, "simulated cleanup failure"):
            with transient_cleanup_lock() as acquired:
                self.assertTrue(acquired)
                # Cleanup can close/reopen its ordinary connection without
                # releasing the separate PostgreSQL session's advisory lock.
                connection.close()
                with transient_cleanup_lock() as contender:
                    self.assertFalse(contender)
                self.assertIsNone(purge_transient_data())
                self.assertTrue(Session.objects.filter(pk=expired.pk).exists())
                raise RuntimeError("simulated cleanup failure")
        # Failure releases the lock, allowing a real cleanup on the test DB.
        counts = purge_transient_data()
        self.assertEqual(counts["sessions"], 1)
        self.assertFalse(Session.objects.filter(pk=expired.pk).exists())
        self.assertTrue(Session.objects.filter(pk=active.pk).exists())
        with transient_cleanup_lock() as acquired:
            self.assertTrue(acquired)
        self.assertNotIn(alias, connections)


class WebSupervisorProcessTests(SimpleTestCase):
    """Use real children to exercise retry, shutdown and process reaping."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.processes = []
        self.addCleanup(self.stop_processes)

    def stop_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=3)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("The supervised process did not reach the expected state")

    def count_starts(self):
        path = self.root / "cleanup-starts"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def start(self, *, failing=False, hanging=False, web_exit=None):
        web = self.root / "web.py"
        web.write_text(textwrap.dedent(f"""
            import os, signal, time
            from pathlib import Path
            root = Path({str(self.root)!r})
            def stop(signum, frame):
                (root / 'web-stopped').write_text(str(signum))
                raise SystemExit(0)
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            (root / 'web-ready').write_text(str(os.getpid()))
            if {web_exit!r} is not None:
                while not (root / 'cleanup-starts').exists():
                    time.sleep(0.01)
                raise SystemExit({web_exit!r})
            while True:
                time.sleep(0.01)
        """))
        cleanup = self.root / "cleanup.py"
        cleanup.write_text(textwrap.dedent(f"""
            import os, signal, time
            from pathlib import Path
            root = Path({str(self.root)!r})
            def stop(signum, frame):
                with (root / 'cleanup-stops').open('a') as stream:
                    stream.write(str(os.getpid()) + '\\n')
                raise SystemExit(0)
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            try:
                descriptor = os.open(root / 'cleanup-running', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                (root / 'overlap').touch()
                raise SystemExit(9)
            os.close(descriptor)
            try:
                with (root / 'cleanup-starts').open('a') as stream:
                    stream.write(str(os.getpid()) + '\\n')
                print('private-data-must-not-be-logged', flush=True)
                if {hanging!r}:
                    while True:
                        time.sleep(0.01)
                time.sleep(0.04)
                raise SystemExit(2 if {failing!r} else 0)
            finally:
                (root / 'cleanup-running').unlink(missing_ok=True)
        """))
        script = textwrap.dedent(f"""
            from accounts.supervisor import WebSupervisor
            supervisor = WebSupervisor(
                [{sys.executable!r}, {str(web)!r}], [{sys.executable!r}, {str(cleanup)!r}],
                interval=0.02, retry_interval=0.02, cleanup_timeout=0.15,
                shutdown_timeout=0.2, poll_interval=0.01,
            )
            raise SystemExit(supervisor.run())
        """)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        process = subprocess.Popen([sys.executable, "-u", "-c", script], env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.processes.append(process)
        return process

    def assert_children_reaped(self):
        pids = []
        if (self.root / "web-ready").exists():
            pids.append(int((self.root / "web-ready").read_text()))
        if (self.root / "cleanup-starts").exists():
            pids.extend(int(value) for value in (self.root / "cleanup-starts").read_text().splitlines())
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_startup_periodic_cleanup_does_not_overlap_and_sigterm_stops_children(self):
        process = self.start()
        self.wait_for(lambda: self.count_starts() >= 3)
        process.terminate()
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, output)
        self.assertEqual((self.root / "web-stopped").read_text(), str(signal.SIGTERM))
        self.assertFalse((self.root / "overlap").exists())
        self.assertNotIn("private-data-must-not-be-logged", output)
        self.assert_children_reaped()

    def test_cleanup_failure_retries_without_stopping_web_and_sigint_is_forwarded(self):
        process = self.start(failing=True)
        self.wait_for(lambda: self.count_starts() >= 2)
        self.assertIsNone(process.poll())
        process.send_signal(signal.SIGINT)
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, output)
        self.assertIn("Transient cleanup failed (exit 2)", output)
        self.assertNotIn("private-data-must-not-be-logged", output)
        self.assertEqual((self.root / "web-stopped").read_text(), str(signal.SIGINT))
        self.assert_children_reaped()

    def test_hung_cleanup_is_terminated_and_retried_while_web_stays_up(self):
        process = self.start(hanging=True)
        self.wait_for(lambda: self.count_starts() >= 2)
        self.assertIsNone(process.poll())
        self.assertTrue((self.root / "cleanup-stops").exists())
        process.terminate()
        output, _ = process.communicate(timeout=5)
        self.assertIn("Transient cleanup timed out", output)
        self.assertFalse((self.root / "overlap").exists())
        self.assert_children_reaped()

    def test_web_failure_exits_with_its_status_and_reaps_cleanup(self):
        process = self.start(hanging=True, web_exit=7)
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 7, output)
        self.assertTrue((self.root / "cleanup-stops").exists())
        self.assert_children_reaped()
