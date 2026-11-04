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
    from .models import AccountApproval, OrganisationAccount

    user = getattr(request, "user", None)
    organisation = None
    pending_organisation_reviews = None
    pending_account_approvals = None
    if user is not None and user.is_authenticated and user.is_active:
        organisation = OrganisationAccount.objects.filter(user=user, status="approved").first()
        if user.is_staff and user.has_perm("accounts.change_organisationaccount"):
            pending_organisation_reviews = OrganisationAccount.objects.filter(status="pending").count()
        if user.is_staff and user.has_perm("accounts.change_accountapproval"):
            pending_account_approvals = AccountApproval.objects.filter(status="pending").count()
    return {
        "organisation": organisation,
        "organisation_name": organisation.name if organisation else "",
        "pending_organisation_reviews": pending_organisation_reviews,
        "pending_account_approvals": pending_account_approvals,
        "account_approval_required": settings.ACCOUNT_APPROVAL_REQUIRED,
        "email_login_enabled": settings.EMAIL_LOGIN_ENABLED,
        "organisation_login_enabled": settings.ORGANISATION_LOGIN_ENABLED,
        "registration_open": registration_is_open(),
        "privacy_contact": settings.PUBLIC_CONTACT_EMAIL,
        "plausible_enabled": bool(settings.PLAUSIBLE_SCRIPT_ID),
        "site_css_version": asset_version(settings.REPO_ROOT / "docs" / "assets" / "site.css"),
        "accounts_css_version": asset_version(settings.BASE_DIR / "static" / "accounts.css"),
        "notices_js_version": asset_version(settings.BASE_DIR / "static" / "notices.js"),
        "admin_navigation_css_version": asset_version(settings.BASE_DIR / "static" / "admin-navigation.css"),
    }
