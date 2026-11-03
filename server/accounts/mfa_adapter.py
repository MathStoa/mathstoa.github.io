from allauth.mfa.adapter import DefaultMFAAdapter
from allauth.mfa.models import Authenticator

from .security_crypto import decrypt, encrypt


class MFAAdapter(DefaultMFAAdapter):
    def encrypt(self, text):
        return encrypt(text.encode("utf-8"), purpose="mfa")

    def decrypt(self, encrypted_text):
        return decrypt(encrypted_text, purpose="mfa").decode("utf-8")

    def can_delete_authenticator(self, authenticator):
        if authenticator.user.is_staff and authenticator.type == Authenticator.Type.TOTP:
            return Authenticator.objects.filter(
                user=authenticator.user, type=Authenticator.Type.TOTP,
            ).exclude(pk=authenticator.pk).exists()
        return super().can_delete_authenticator(authenticator)
