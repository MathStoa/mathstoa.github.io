"""Authenticate an ORCID without fetching the person's public record."""
import re

from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView, OAuth2LoginView
from allauth.socialaccount.providers.orcid.views import OrcidOAuth2Adapter
from django.core.exceptions import ValidationError

from submissions.forms import normalize_orcid


class MinimalOrcidOAuth2Adapter(OrcidOAuth2Adapter):
    def complete_login(self, request, app, token, **kwargs):
        # This value comes from the server-to-server code exchange after allauth
        # validates browser-bound state, never from a query parameter or form.
        response = kwargs.get("response")
        identifier = response.get("orcid") if isinstance(response, dict) else None
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]", identifier):
            raise OAuth2Error("The identity provider did not return a valid ORCID.")
        try:
            normalize_orcid(identifier)
        except ValidationError:
            raise OAuth2Error("The identity provider did not return a valid ORCID.") from None
        return self.get_provider().sociallogin_from_response(
            request, {"orcid-identifier": {"path": identifier}},
        )


oauth2_login = OAuth2LoginView.adapter_view(MinimalOrcidOAuth2Adapter)
oauth2_callback = OAuth2CallbackView.adapter_view(MinimalOrcidOAuth2Adapter)
