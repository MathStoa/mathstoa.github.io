from django.conf import settings

from .policy import registration_is_open


def account_options(request):
    return {
        "email_login_enabled": settings.EMAIL_LOGIN_ENABLED,
        "registration_open": registration_is_open(),
        "privacy_contact": settings.PUBLIC_CONTACT_EMAIL,
    }
