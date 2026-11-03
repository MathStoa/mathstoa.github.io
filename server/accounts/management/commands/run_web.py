"""Container entry point: Gunicorn plus automatic transient-data cleanup."""
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

from accounts.supervisor import WebSupervisor


class Command(BaseCommand):
    help = "Run Gunicorn and clean expired transient data at startup and hourly."
    requires_system_checks = []

    def handle(self, *args, **options):
        server = Path(__file__).resolve().parents[3]
        web_command = [
            sys.executable, "-m", "gunicorn", "--chdir", str(server),
            "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2",
            "--access-logfile", "-", "--error-logfile", "-",
            "--access-logformat", '%(t)s "%(m)s" %(s)s %(b)s',
        ]
        cleanup_command = [sys.executable, str(server / "manage.py"), "purge_transient_data", "--skip-checks"]
        status = WebSupervisor(web_command, cleanup_command, log=self.stdout.write).run()
        if status:
            raise SystemExit(status)
