import hashlib
from functools import lru_cache

from django.conf import settings

from .policy import registration_is_open


@lru_cache(maxsize=8)
def _digest(path, mtime_ns):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def asset_version(path):
    """Cache-busting tag: the same sha256[:12] as the static pages' ?v= values."""
    try:
        return _digest(path, path.stat().st_mtime_ns)
    except OSError:
        return ""


def account_options(request):
    return {
        "email_login_enabled": settings.EMAIL_LOGIN_ENABLED,
        "registration_open": registration_is_open(),
        "privacy_contact": settings.PUBLIC_CONTACT_EMAIL,
        "site_css_version": asset_version(settings.REPO_ROOT / "docs" / "assets" / "site.css"),
        "accounts_css_version": asset_version(settings.BASE_DIR / "static" / "accounts.css"),
    }
