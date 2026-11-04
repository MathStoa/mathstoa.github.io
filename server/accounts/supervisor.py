"""Supervise the web server and independent, bounded periodic subprocesses."""
from dataclasses import dataclass
import os
import signal
import subprocess
import time


@dataclass
class _PeriodicJob:
    command: list
    label: str
    interval: float
    retry_interval: float
    timeout: float
    child: object = None
    started: float = 0
    next_run: float = 0
    stop_deadline: float | None = None


class WebSupervisor:
    def __init__(self, web_command, cleanup_command, *, log=print,
                 interval=3600, retry_interval=300, cleanup_timeout=300,
                 notification_command=None, notification_interval=60,
                 notification_retry_interval=60, notification_timeout=360,
                 shutdown_timeout=30, poll_interval=0.2):
        self.web_command = web_command
        self.cleanup_command = cleanup_command
        self.log = log
        self.interval = interval
        self.retry_interval = retry_interval
        self.cleanup_timeout = cleanup_timeout
        self.notification_command = notification_command
        self.notification_interval = notification_interval
        self.notification_retry_interval = notification_retry_interval
        self.notification_timeout = notification_timeout
        self.shutdown_timeout = shutdown_timeout
        self.poll_interval = poll_interval
        self.stop_signal = None

    def request_stop(self, signum, _frame):
        self.stop_signal = signum

    @staticmethod
    def signal_child(child, signum):
        if child is not None:
            try:
                # Each child owns a process group, including any Gunicorn workers.
                # The group can outlive its leader after a web-server failure.
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    def stop_children(self, children, *, grace, signum=signal.SIGTERM):
        children = [child for child in children if child is not None]
        for child in children:
            self.signal_child(child, signum)
        deadline = time.monotonic() + grace
        while any(child.poll() is None for child in children) and time.monotonic() < deadline:
            time.sleep(min(self.poll_interval, 0.1))
        for child in children:
            self.signal_child(child, signal.SIGKILL)
        for child in children:
            child.wait()

    def _poll_job(self, job, now):
        if job.child is not None:
            status = job.child.poll()
            if status is not None:
                if job.stop_deadline is not None:
                    # Also reap descendants that ignored termination of the leader.
                    self.signal_child(job.child, signal.SIGKILL)
                elif status == 0:
                    self.log(f"{job.label} completed or another instance holds the job lock.")
                else:
                    self.log(f"{job.label} failed (exit {status}); retrying after {job.retry_interval:g} seconds.")
                succeeded = status == 0 and job.stop_deadline is None
                job.child = None
                job.stop_deadline = None
                job.next_run = now + (job.interval if succeeded else job.retry_interval)
            elif job.stop_deadline is not None:
                if now >= job.stop_deadline:
                    self.signal_child(job.child, signal.SIGKILL)
            elif now - job.started >= job.timeout:
                # Do not block the web-health check or the other job's schedule
                # while waiting for a stuck worker to terminate.
                self.signal_child(job.child, signal.SIGTERM)
                job.stop_deadline = now + min(5, self.shutdown_timeout)
                self.log(f"{job.label} timed out; retrying after {job.retry_interval:g} seconds.")
        if job.child is None and now >= job.next_run:
            try:
                # Never relay worker tracebacks, credentials or record contents.
                # Manual commands expose aggregate counts only.
                job.child = subprocess.Popen(job.command, start_new_session=True,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                job.started = time.monotonic()
            except OSError:
                self.log(f"{job.label} could not start; retrying after {job.retry_interval:g} seconds.")
                job.next_run = now + job.retry_interval

    def run(self):
        previous_handlers = {sig: signal.signal(sig, self.request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        web = None
        jobs = [_PeriodicJob(self.cleanup_command, "Transient cleanup", self.interval,
                             self.retry_interval, self.cleanup_timeout)]
        if self.notification_command is not None:
            jobs.append(_PeriodicJob(self.notification_command, "Notification delivery",
                                     self.notification_interval, self.notification_retry_interval,
                                     self.notification_timeout))
        try:
            web = subprocess.Popen(self.web_command, start_new_session=True)
            while self.stop_signal is None:
                web_status = web.poll()
                if web_status is not None:
                    return web_status if web_status >= 0 else 128 - web_status
                for job in jobs:
                    self._poll_job(job, time.monotonic())
                time.sleep(self.poll_interval)
            return 0
        finally:
            self.stop_children([web, *(job.child for job in jobs)], grace=self.shutdown_timeout,
                               signum=self.stop_signal or signal.SIGTERM)
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
