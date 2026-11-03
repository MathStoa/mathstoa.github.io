from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_safe

PUBLIC_PAGES = {"index.html", "about.html", "project.html", "404.html"}


@require_safe
def public_page(request, page="index.html"):
    if page not in PUBLIC_PAGES:
        raise Http404
    text = (settings.REPO_ROOT / "docs" / page).read_text(encoding="utf-8")
    text = text.replace('href="assets/', 'href="/assets/').replace('src="assets/', 'src="/assets/')
    if request.user.is_authenticated:
        account_link = '<a href="/submissions/">My submissions</a>'
    else:
        account_link = '<a href="/accounts/login/">Sign in</a>'
    text = text.replace('<nav aria-label="Main navigation">', '<nav aria-label="Main navigation">' + account_link)
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
