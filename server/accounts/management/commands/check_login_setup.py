"""Report required external configuration without printing credentials."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check login configuration and print exact provider callback URLs (no secrets)."

    def add_arguments(self, parser):
        parser.add_argument("--require-all", action="store_true", help="Fail unless all four provider connections and email are configured.")

    def handle(self, *args, **options):
        configured = settings.SOCIALACCOUNT_PROVIDERS
        missing = []
        for key, label, callback in (
            ("orcid", "ORCID", "/accounts/orcid/login/callback/"),
            ("github", "GitHub", "/accounts/github/login/callback/"),
            ("gitlab", "GitLab", "/accounts/gitlab/login/callback/"),
            ("openid_connect", "Institution", "/accounts/oidc/institution/login/callback/"),
        ):
            enabled = key in configured
            self.stdout.write(f"{label}: {'configured' if enabled else 'not configured'}")
            self.stdout.write(f"  Callback: {settings.PUBLIC_BASE_URL}{callback}")
            if not enabled:
                missing.append(label)
        if settings.EMAIL_LOGIN_ENABLED:
            self.stdout.write("Email: local console demonstration" if settings.DEBUG else "Email: SMTP configured")
        else:
            self.stdout.write("Email: disabled")
            missing.append("Email")
        self.stdout.write(f"Organisation email codes: {'enabled' if settings.ORGANISATION_LOGIN_ENABLED else 'disabled'}")
        self.stdout.write("Credentials still need a real provider sign-in test after deployment.")
        if options["require_all"] and missing:
            raise CommandError("Still to configure: " + ", ".join(missing))
