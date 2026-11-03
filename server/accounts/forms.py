from django import forms
from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.forms import DisconnectForm


class PasswordlessDisconnectForm(DisconnectForm):
    def clean(self):
        # allauth's default disconnect check assumes a password is needed for
        # email recovery. Our email codes are a complete sign-in method.
        cleaned = forms.Form.clean(self)
        account = cleaned.get("account")
        if account:
            get_adapter().validate_disconnect(account, self.accounts)
        return cleaned
