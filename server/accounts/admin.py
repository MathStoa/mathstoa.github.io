from allauth.mfa.models import Authenticator
from django.contrib import admin

# Authenticator material must not be viewed, replaced, or reset in ordinary admin.
if admin.site.is_registered(Authenticator):
    admin.site.unregister(Authenticator)
