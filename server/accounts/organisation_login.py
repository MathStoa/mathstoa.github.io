"""Organisation entry point using the existing bounded email-code flow."""
from allauth.account.forms import RequestLoginCodeForm
from allauth.account.internal.stagekit import stash_login, unstash_login
from allauth.account.views import RequestLoginCodeView
from django.conf import settings
from django.http import Http404
from django.urls import reverse

from .models import OrganisationAccount


ORGANISATION_LOGIN_STATE = "mathstoa_organisation_login"


def organisation_code_confirmation_allowed(request):
    """Only this entrance may use shared code confirmation when personal email is off."""
    if not settings.ORGANISATION_LOGIN_ENABLED:
        return False
    login = unstash_login(request, peek=True)
    if login is None or login.state.get(ORGANISATION_LOGIN_STATE) is not True:
        return False
    # Unknown addresses keep allauth's indistinguishable confirmation stage.
    if login.user is None:
        return True
    return OrganisationAccount.objects.filter(
        user_id=login.user.pk, user__is_active=True, status="approved", email__iexact=login.email,
    ).exists()


def organisation_account(user):
    """Read current moderation state instead of a cached reverse relation."""
    if user is None or not user.is_authenticated or user.pk is None:
        return None
    return OrganisationAccount.objects.filter(user_id=user.pk).first()


def organisation_access_denied(user):
    organisation = organisation_account(user)
    return organisation is not None and (
        organisation.status != "approved" or not organisation.user.is_active
    )


class OrganisationLoginCodeForm(RequestLoginCodeForm):
    def clean_email(self):
        # Retain allauth's email validation, enumeration protection and per-email
        # rate limiting. Its confirmation stage enforces expiry and single use.
        email = super().clean_email()
        user = getattr(self, "_user", None)
        if user is not None and not OrganisationAccount.objects.filter(
            user=user, user__is_active=True, status="approved", email__iexact=email,
        ).exists():
            self._user = None
        return email


class OrganisationLoginView(RequestLoginCodeView):
    template_name = "account/organisation_login.html"
    form_class = OrganisationLoginCodeForm

    def dispatch(self, request, *args, **kwargs):
        if not settings.ORGANISATION_LOGIN_ENABLED and request.method not in {"GET", "HEAD"}:
            raise Http404("Organisation sign-in is unavailable.")
        return super().dispatch(request, *args, **kwargs)

    def get_form_class(self):
        return self.form_class

    def form_valid(self, form):
        response = super().form_valid(form)
        login = unstash_login(self.request, peek=True)
        if login is not None:
            login.state[ORGANISATION_LOGIN_STATE] = True
            stash_login(self.request, login)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["request_login_code_url"] = reverse("organisation_login")
        return context
