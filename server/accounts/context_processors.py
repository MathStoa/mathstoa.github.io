from django.conf import settings


def account_options(request):
    return {"email_login_enabled": settings.EMAIL_LOGIN_ENABLED, "privacy_contact": settings.PUBLIC_CONTACT_EMAIL}
