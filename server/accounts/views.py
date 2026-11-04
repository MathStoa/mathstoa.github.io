import re

from django.conf import settings
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import requires_csrf_token
from django.views.decorators.http import require_safe

PUBLIC_PAGES = {"index.html", "about.html", "project.html", "404.html"}


@require_safe
def public_page(request, page="index.html"):
    if page not in PUBLIC_PAGES:
        raise Http404
    text = (settings.REPO_ROOT / "docs" / page).read_text(encoding="utf-8")
    text = text.replace('href="assets/', 'href="/assets/').replace('src="assets/', 'src="/assets/')
    # The static pages describe the legacy GitHub route; the service takes
    # submissions itself, and its pages link the same account and privacy routes.
    text = text.replace('href="submit.html"', 'href="/submissions/new/"')
    # Public content stays static; navigation uses the same permission-aware
    # template as account pages. Render with the request for context and CSRF.
    navigation = render_to_string("includes/main_navigation.html", request=request)
    text = re.sub(
        r'<nav aria-label="Main navigation">.*?</nav>',
        lambda match: navigation, text, count=1, flags=re.DOTALL,
    )
    # The standalone recovery page needs a canonical base on GitHub Pages only.
    # On the service, navigation and CSRF forms must target this request's origin.
    text = text.replace('<base href="https://math-stoa.org/">', '')
    text = text.replace('<nav aria-label="Footer navigation">', '<nav aria-label="Footer navigation"><a href="/privacy/">Privacy</a>')
    response = HttpResponse(text)
    patch_vary_headers(response, ["Cookie"])
    return response


@require_safe
def site_asset(request, path):
    root = (settings.REPO_ROOT / "docs" / "assets").resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise Http404
    return FileResponse(target.open("rb"))


@require_safe
def old_submit(request):
    return redirect("submissions:create")


@require_safe
def privacy(request):
    return render(request, "privacy.html")


@require_safe
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


@requires_csrf_token
def csrf_failure(request, reason=""):
    # Keep the rejection and its security logging in CsrfViewMiddleware. Show
    # generic recovery guidance without echoing the failed request or reason.
    return render(request, "csrf_failure.html", status=403)
