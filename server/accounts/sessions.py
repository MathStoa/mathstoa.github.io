"""Django database sessions with encrypted, authenticated JSON payloads."""
import logging

from cryptography.fernet import InvalidToken
from django.contrib.sessions.backends.db import SessionStore as DatabaseSessionStore

from .security_crypto import decrypt, encrypt


class SessionStore(DatabaseSessionStore):
    def encode(self, session_dict):
        return encrypt(self.serializer().dumps(session_dict), purpose="sessions")

    def decode(self, session_data):
        try:
            decoded = self.serializer().loads(decrypt(session_data, purpose="sessions"))
            if isinstance(decoded, dict):
                return decoded
        except (InvalidToken, UnicodeError, ValueError, TypeError):
            # Never log the payload, authentication codes, or key material.
            logging.getLogger("django.security.SuspiciousSession").warning(
                "Encrypted session could not be authenticated."
            )
        return {}
