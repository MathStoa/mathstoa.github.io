"""Anonymous applications reserve a separate, inactive organisation login."""
import secrets

from allauth.account.models import EmailAddress
from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_safe

from .middleware import admin_network_key, limit_action
from .models import OrganisationAccount
from .policy import registration_is_open


class OrganisationRegistrationForm(forms.Form):
    name = forms.CharField(label="Organisation name", max_length=150)
    website = forms.URLField(max_length=500, assume_scheme="https")
    description = forms.CharField(max_length=1500, widget=forms.Textarea(attrs={"rows": 4}), help_text="Briefly describe the organisation and its mathematical work.")
    email = forms.EmailField(label="Organisation email", max_length=254, help_text="Use a mailbox controlled by your organisation. Sign-in codes and the moderation decision will be sent here. Use a different address from any personal account.")
    representative_name = forms.CharField(label="Your name", max_length=150)
    authority = forms.CharField(label="Your role and authority to represent the organisation", max_length=1500, widget=forms.Textarea(attrs={"rows": 4}), help_text="Explain your role and how a moderator can confirm you may represent the organisation. Include public evidence where available.")

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

def _register(cleaned):
    email = cleaned["email"]
    User = get_user_model()
    with transaction.atomic():
        if (EmailAddress.objects.filter(email__iexact=email).exists()
                or User.objects.filter(email__iexact=email).exists()
                or OrganisationAccount.objects.filter(email__iexact=email).exists()):
            return
        user = User.objects.create_user(
            username="organisation-" + secrets.token_hex(12), email=email,
            first_name=cleaned["name"], password=None, is_active=False,
        )
        EmailAddress.objects.create(user=user, email=email, primary=True, verified=False)
        OrganisationAccount.objects.create(user=user, **cleaned)


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("socialaccount_connections")
    available = settings.ORGANISATION_LOGIN_ENABLED and registration_is_open()
    if not available:
        return render(request, "account/organisation_register.html", status=403)
    if request.method == "POST":
        limited = limit_action(request, action="organisation_register", key=admin_network_key(request), retry_after=3600)
        if limited:
            return limited
    form = OrganisationRegistrationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            _register(form.cleaned_data)
        except IntegrityError:
            # Simultaneous applications for the same mailbox have one winner.
            if not OrganisationAccount.objects.filter(email__iexact=form.cleaned_data["email"]).exists():
                raise
        return redirect("organisation_registration_received")
    return render(request, "account/organisation_register.html", {"form": form})


@require_safe
def received(request):
    return render(request, "account/organisation_registration_received.html")
