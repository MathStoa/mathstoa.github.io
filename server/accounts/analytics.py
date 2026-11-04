"""Opt-in readership analytics for public archive content only."""
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.db import DatabaseError
from django.utils.html import format_html

from .context_processors import asset_version


def is_public_project(project_id):
    from submissions.views import public_archive

    return any(project["id"] == project_id for project in public_archive()["projects"])


def tracking_script(request, page):
    if (
        not settings.PLAUSIBLE_SCRIPT_ID
        or settings.DEBUG
        or page not in {"index.html", "about.html", "project.html"}
        or request.user.is_staff
        or request.get_host() != urlsplit(settings.PUBLIC_BASE_URL).netloc
    ):
        return ""

    path = "/" if page == "index.html" else f"/{page}"
    if page == "project.html":
        # Only an ID already disclosed in the public archive may enter a
        # report. Arbitrary query strings can contain contact details/tokens.
        project_id = request.GET.get("id", "")
        if project_id:
            try:
                known_project = is_public_project(project_id)
            except (OSError, ValueError, KeyError, TypeError, DatabaseError):
                # Statistics must not make an otherwise usable page fail.
                return ""
            if known_project:
                path += "/" + quote(project_id, safe="")

    request.plausible_tracking = True
    return format_html(
        '<script defer src="/assets/analytics.js?v={}" data-script-id="{}" data-page-url="{}"></script>',
        asset_version(settings.REPO_ROOT / "docs" / "assets" / "analytics.js"),
        settings.PLAUSIBLE_SCRIPT_ID,
        settings.PUBLIC_BASE_URL + path,
    )
