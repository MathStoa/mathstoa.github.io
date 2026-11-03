"""Purpose-separated authenticated encryption for private database values.

The master secret stays in the host's secret store, outside database backups.
This protects database-only disclosure, not compromise of the running service.
"""
import base64

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

PREFIX = "mathstoa-encrypted-v1:"


def _cipher(purpose):
    keys = [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
    ciphers = []
    for master in keys:
        derived = HKDF(
            algorithm=hashes.SHA256(), length=32,
            salt=b"mathstoa.database-encryption.v1",
            info=("mathstoa:" + purpose).encode("ascii"),
        ).derive(master.encode("utf-8"))
        ciphers.append(Fernet(base64.urlsafe_b64encode(derived)))
    return MultiFernet(ciphers)


def encrypt(value, *, purpose):
    return PREFIX + _cipher(purpose).encrypt(value).decode("ascii")


def decrypt(value, *, purpose):
    # Never accept old signed-but-readable sessions or plaintext MFA secrets.
    if not isinstance(value, str) or not value.startswith(PREFIX):
        raise InvalidToken
    try:
        token = value[len(PREFIX):].encode("ascii")
    except UnicodeEncodeError as exc:
        raise InvalidToken from exc
    return _cipher(purpose).decrypt(token)
