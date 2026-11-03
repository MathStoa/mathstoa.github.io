"""Supervise the web server and a bounded, periodic cleanup subprocess."""
import os
import signal
import subprocess
import time


class WebSupervisor:
    def __init__(self, web_command, cleanup_command, *, log=print,
                 interval=3600, retry_interval=300, cleanup_timeout=300,
                 shutdown_timeout=30, poll_interval=0.2):
        self.web_command = web_command
        self.cleanup_command = cleanup_command
        self.log = log
        self.interval = interval
        self.retry_interval = retry_interval
        self.cleanup_timeout = cleanup_timeout
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

    def run(self):
        previous_handlers = {sig: signal.signal(sig, self.request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        web = cleanup = None
        next_cleanup = time.monotonic()
        started_cleanup = None
        try:
            web = subprocess.Popen(self.web_command, start_new_session=True)
            while self.stop_signal is None:
                web_status = web.poll()
                if web_status is not None:
                    return web_status if web_status >= 0 else 128 - web_status
                now = time.monotonic()
                if cleanup is not None:
                    status = cleanup.poll()
                    if status is not None:
                        if status == 0:
                            self.log("Transient cleanup completed or another instance holds the cleanup lock.")
                        else:
                            self.log(f"Transient cleanup failed (exit {status}); retrying after {self.retry_interval:g} seconds.")
                        cleanup = None
                        next_cleanup = now + (self.interval if status == 0 else self.retry_interval)
                    elif now - started_cleanup >= self.cleanup_timeout:
                        self.stop_children([cleanup], grace=min(5, self.shutdown_timeout))
                        self.log(f"Transient cleanup timed out; retrying after {self.retry_interval:g} seconds.")
                        cleanup = None
                        next_cleanup = time.monotonic() + self.retry_interval
                if cleanup is None and now >= next_cleanup:
                    try:
                        # Never relay cleanup tracebacks, credentials or record contents.
                        # Operators can run the manual command to inspect aggregate counts.
                        cleanup = subprocess.Popen(self.cleanup_command, start_new_session=True,
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        started_cleanup = time.monotonic()
                    except OSError:
                        self.log(f"Transient cleanup could not start; retrying after {self.retry_interval:g} seconds.")
                        next_cleanup = now + self.retry_interval
                time.sleep(self.poll_interval)
            return 0
        finally:
            self.stop_children([web, cleanup], grace=self.shutdown_timeout,
                               signum=self.stop_signal or signal.SIGTERM)
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
