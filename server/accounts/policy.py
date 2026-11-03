from django.conf import settings


def registration_is_open():
    """Closing registration never disables an existing account's sign-in."""
    return settings.PUBLIC_REGISTRATION_ENABLED and (
        settings.DEBUG or bool(settings.PUBLIC_CONTACT_EMAIL.strip())
    )
