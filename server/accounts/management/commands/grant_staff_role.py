from allauth.socialaccount.models import SocialAccount
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Grant staff access to an existing provider account; MFA remains mandatory."

    def add_arguments(self, parser):
        parser.add_argument("--provider", required=True)
        parser.add_argument("--uid", required=True)
        parser.add_argument("--superuser", action="store_true", help="Grant full administration for the initial operator.")

    def handle(self, *args, **options):
        account = SocialAccount.objects.select_related("user").filter(
            provider=options["provider"], uid=options["uid"],
        ).first()
        if account is None or not account.user.is_active:
            raise CommandError("An active account must first sign in using that provider identity.")
        user = account.user
        user.is_staff = True
        if options["superuser"]:
            user.is_superuser = True
        # Changing the authentication hash also invalidates existing sessions.
        user.set_unusable_password()
        user.save(update_fields=["is_staff", "is_superuser", "password"])
        self.stdout.write("Staff role granted. Sign in again and complete authenticator setup before using administration.")
