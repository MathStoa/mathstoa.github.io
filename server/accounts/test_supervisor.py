import os
from io import StringIO
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
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase
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

    def notification_starts(self):
        path = self.root / "notification-starts"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def start(self, *, failing=False, hanging=False, web_exit=None,
              notifications=False, notification_failing=False, notification_hanging=False,
              notification_ignore_term=False, cleanup_interval=0.02,
              notification_interval=0.02, notification_timeout=0.15, shutdown_timeout=0.2):
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
        notification = self.root / "notification.py"
        notification.write_text(textwrap.dedent(f"""
            import os, signal, time
            from pathlib import Path
            root = Path({str(self.root)!r})
            def stop(signum, frame):
                with (root / 'notification-stops').open('a') as stream:
                    stream.write(str(os.getpid()) + '\\n')
                if not {notification_ignore_term!r}:
                    raise SystemExit(0)
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            with (root / 'notification-starts').open('a') as stream:
                stream.write(str(os.getpid()) + '\\n')
            print('recipient-private-data-must-not-be-logged', flush=True)
            print('smtp-secret-must-not-be-logged', file=__import__('sys').stderr, flush=True)
            if {notification_hanging!r}:
                while True:
                    time.sleep(0.01)
            time.sleep(0.04)
            raise SystemExit(3 if {notification_failing!r} else 0)
        """))
        notification_command = [sys.executable, str(notification)] if notifications else None
        script = textwrap.dedent(f"""
            from accounts.supervisor import WebSupervisor
            supervisor = WebSupervisor(
                [{sys.executable!r}, {str(web)!r}], [{sys.executable!r}, {str(cleanup)!r}],
                interval={cleanup_interval!r}, retry_interval=0.02, cleanup_timeout=0.15,
                notification_command={notification_command!r},
                notification_interval={notification_interval!r}, notification_retry_interval=0.02,
                notification_timeout={notification_timeout!r},
                shutdown_timeout={shutdown_timeout!r}, poll_interval=0.01,
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
        if (self.root / "notification-starts").exists():
            pids.extend(int(value) for value in (self.root / "notification-starts").read_text().splitlines())
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

    def test_notifications_run_independently_of_hourly_cleanup_and_hide_child_output(self):
        process = self.start(notifications=True, cleanup_interval=30)
        self.wait_for(lambda: self.notification_starts() >= 3)
        self.assertEqual(self.count_starts(), 1)
        self.assertIsNone(process.poll())
        process.terminate()
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, output)
        self.assertNotIn("recipient-private-data", output)
        self.assertNotIn("smtp-secret", output)
        self.assert_children_reaped()

    def test_notification_failure_retries_while_cleanup_and_web_continue(self):
        process = self.start(notifications=True, notification_failing=True)
        self.wait_for(lambda: self.notification_starts() >= 3 and self.count_starts() >= 3)
        self.assertIsNone(process.poll())
        process.send_signal(signal.SIGINT)
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, output)
        self.assertIn("Notification delivery failed (exit 3)", output)
        self.assertIn("Transient cleanup completed", output)
        self.assertNotIn("smtp-secret", output)
        self.assert_children_reaped()

    def test_hung_notification_is_killed_and_retried_without_overlapping(self):
        process = self.start(notifications=True, notification_hanging=True, notification_ignore_term=True)
        self.wait_for(lambda: self.notification_starts() >= 2)
        self.assertGreaterEqual(self.count_starts(), 3)
        pids = [int(value) for value in (self.root / "notification-starts").read_text().splitlines()]
        with self.assertRaises(ProcessLookupError):
            os.kill(pids[0], 0)
        self.assertIsNone(process.poll())
        process.terminate()
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, output)
        self.assertIn("Notification delivery timed out", output)
        self.assert_children_reaped()

    def test_cleanup_continues_during_notification_termination_grace(self):
        process = self.start(notifications=True, notification_hanging=True, notification_ignore_term=True,
                             notification_timeout=0.1, shutdown_timeout=2)
        self.wait_for(lambda: (self.root / "notification-stops").exists())
        count = self.count_starts()
        self.wait_for(lambda: self.count_starts() >= count + 3)
        self.assertEqual(self.notification_starts(), 1)
        pid = int((self.root / "notification-starts").read_text().strip())
        os.kill(pid, 0)  # The first worker is still within its termination grace.
        process.terminate()
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, output)
        self.assert_children_reaped()

    def test_web_failure_reaps_both_background_workers(self):
        process = self.start(hanging=True, notifications=True, notification_hanging=True)
        self.wait_for(lambda: (self.root / "web-ready").exists()
                      and self.count_starts() >= 1 and self.notification_starts() >= 1)
        os.kill(int((self.root / "web-ready").read_text()), signal.SIGKILL)
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 128 + signal.SIGKILL, output)
        self.assert_children_reaped()


class NotificationWorkerCommandTests(SimpleTestCase):
    def test_bounded_worker_prints_only_known_aggregate_counts(self):
        output = StringIO()
        counts = dict(sent=2, failed=0, suppressed=0, cancelled=0, expired=1, remaining=4,
                      internal_detail="private-recipient@example.invalid")
        with patch("submissions.management.commands.process_notification_queue.process_notification_queue",
                   return_value=counts) as process:
            call_command("process_notification_queue", batch_size=7, stdout=output)
        process.assert_called_once_with(batch_size=7)
        self.assertEqual(output.getvalue(), "sent=2, failed=0, suppressed=0, cancelled=0, expired=1, remaining=4\n")

    def test_delivery_failures_signal_nonzero_status_without_message_details(self):
        output = StringIO()
        counts = dict(sent=1, failed=1, suppressed=0, cancelled=0, expired=0, remaining=2)
        with patch("submissions.management.commands.process_notification_queue.process_notification_queue",
                   return_value=counts) as process:
            with self.assertRaisesMessage(CommandError, "attempted messages will not be retried") as caught:
                call_command("process_notification_queue", stdout=output)
        self.assertNotEqual(caught.exception.returncode, 0)
        process.assert_called_once_with(batch_size=20)
        self.assertEqual(output.getvalue(), "sent=1, failed=1, suppressed=0, cancelled=0, expired=0, remaining=2\n")

    def test_invalid_batch_sizes_never_start_delivery(self):
        with patch("submissions.management.commands.process_notification_queue.process_notification_queue") as process:
            for size in (-1, 0, 21, 1000):
                with self.subTest(size=size), self.assertRaisesMessage(CommandError, "between 1 and 20"):
                    call_command("process_notification_queue", batch_size=size)
        process.assert_not_called()

    def test_backend_exception_cannot_disclose_recipient_or_credentials(self):
        output = StringIO()
        with patch("submissions.management.commands.process_notification_queue.process_notification_queue",
                   side_effect=RuntimeError("private-recipient@example.invalid smtp-secret")):
            with self.assertRaisesMessage(CommandError, "Notification delivery could not complete; retry later.") as caught:
                call_command("process_notification_queue", stdout=output)
        self.assertNotIn("private-recipient", str(caught.exception))
        self.assertNotIn("smtp-secret", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(output.getvalue(), "")

    def test_container_entrypoint_starts_bounded_queue_worker(self):
        from submissions.management.commands.process_notification_queue import Command

        with patch("accounts.management.commands.run_web.WebSupervisor") as supervisor:
            supervisor.return_value.run.return_value = 0
            call_command("run_web", stdout=StringIO())
        args, kwargs = supervisor.call_args
        self.assertIn("purge_transient_data", args[1])
        worker_command = kwargs["notification_command"]
        # Exercise the real CLI parser: call_command alone accepts options that
        # Django omits from the CLI when requires_system_checks is empty.
        counts = dict(sent=0, failed=0, suppressed=0, cancelled=0, expired=0, remaining=0)
        with patch("submissions.management.commands.process_notification_queue.process_notification_queue",
                   return_value=counts) as process:
            Command(stdout=StringIO()).run_from_argv(worker_command[1:])
        process.assert_called_once_with(batch_size=20)
        supervisor.return_value.run.assert_called_once_with()


class NotificationWorkerCommandStateTests(TestCase):
    def test_timed_out_claim_reports_failure_without_retrying_an_ambiguous_send(self):
        from django.contrib.auth import get_user_model
        from submissions.models import NotificationDelivery, Submission, SubmissionRevision

        owner = get_user_model().objects.create_user(username="worker-test-author")
        submission = Submission.objects.create(owner=owner, title="Worker test")
        revision = SubmissionRevision.objects.create(submission=submission, number=1, snapshot=submission.snapshot())
        job = NotificationDelivery.objects.create(
            revision=revision, account=owner, kind="confirmation", recipient_digest="a" * 64,
            status="sending", attempted_at=timezone.now() - timedelta(minutes=11),
        )
        with patch("submissions.notifications.EmailMessage.send") as send:
            with self.assertRaisesMessage(CommandError, "attempted messages will not be retried") as caught:
                call_command("process_notification_queue", stdout=StringIO())
            self.assertNotEqual(caught.exception.returncode, 0)
            job.refresh_from_db()
            self.assertEqual(job.status, "failed")
            self.assertIsNotNone(job.finished_at)
            # The supervisor can restart the command: the failed claim stays terminal.
            call_command("process_notification_queue", stdout=StringIO())
        send.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
