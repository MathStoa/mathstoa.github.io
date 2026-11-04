"""Readership tracking must never expose private routes or query values."""
from html.parser import HTMLParser
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import RequestFactory, TestCase, override_settings

from .analytics import tracking_script


class ScriptParser(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.scripts = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts.append(dict(attrs))


@override_settings(DEBUG=False, PLAUSIBLE_SCRIPT_ID="pa-test_MathStoa", PUBLIC_BASE_URL="https://testserver")
class AnalyticsTests(TestCase):
    def analytics_script(self, response):
        scripts = ScriptParser(response.content.decode()).scripts
        return [item for item in scripts if item.get("src", "").startswith("/assets/analytics.js?")]

    def assert_untracked(self, response):
        self.assertEqual(self.analytics_script(response), [])
        self.assertNotIn("plausible.io", response["Content-Security-Policy"])

    def test_public_pages_have_one_safe_initializer_and_narrow_csp(self):
        for path, canonical in (("/", "/"), ("/index.html", "/"), ("/about.html", "/about.html")):
            with self.subTest(path=path):
                response = self.client.get(path + "?search=private&email=reader%40example.org&utm_term=secret")
                self.assertEqual(response.status_code, 200)
                scripts = self.analytics_script(response)
                self.assertEqual(len(scripts), 1)
                self.assertEqual(scripts[0]["data-page-url"], "https://testserver" + canonical)
                self.assertEqual(scripts[0]["data-script-id"], "pa-test_MathStoa")
                policy = response["Content-Security-Policy"]
                self.assertIn("script-src 'self' https://plausible.io/js/pa-test_MathStoa.js;", policy)
                self.assertIn("connect-src 'self' https://plausible.io/api/event;", policy)
                self.assertNotIn("unsafe-inline", policy)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertIn("Cookie", response["Vary"])

    def test_disabled_debug_and_other_hosts_do_not_load_tracking(self):
        for overrides in ({"PLAUSIBLE_SCRIPT_ID": ""}, {"DEBUG": True}, {"PUBLIC_BASE_URL": "https://math-stoa.org"}):
            with self.subTest(overrides=overrides), override_settings(**overrides):
                self.assert_untracked(self.client.get("/"))

    def test_staff_excluded_and_ordinary_readers_included(self):
        reader = get_user_model().objects.create_user(username="reader")
        self.client.force_login(reader)
        self.assertEqual(len(self.analytics_script(self.client.get("/"))), 1)
        # Test the predicate directly as staff without MFA may be redirected
        # before the public view is invoked.
        reader.is_staff = True
        request = RequestFactory().get("/")
        request.user = reader
        self.assertEqual(tracking_script(request, "index.html"), "")
        self.assertFalse(getattr(request, "plausible_tracking", False))

    def test_private_error_and_service_pages_never_load_tracking(self):
        for path in (
            "/accounts/login/", "/accounts/privacy/", "/submissions/", "/submissions/new/",
            "/admin/", "/verify/", "/privacy/", "/404.html", "/missing-page",
            "/data/projects.json", "/health/", "/submit.html",
        ):
            with self.subTest(path=path):
                self.assert_untracked(self.client.get(path))

    @patch("submissions.views.public_archive", return_value={"projects": [{"id": "public-project"}]})
    def test_only_known_public_project_identifiers_reach_reports(self, archive):
        for project_id, reported_path in (
            ("public-project", "/project.html/public-project"),
            ("private-draft", "/project.html"),
            ("reader@example.org", "/project.html"),
            ('\"><script>alert(1)</script>', "/project.html"),
        ):
            with self.subTest(project_id=project_id):
                response = self.client.get("/project.html", {"id": project_id, "utm_term": "private"})
                self.assertEqual(self.analytics_script(response)[0]["data-page-url"], "https://testserver" + reported_path)

    @patch("submissions.views.public_archive", return_value={"projects": [{"id": "public-project"}]})
    def test_dashboard_project_links_redirect_only_for_public_projects(self, archive):
        response = self.client.get("/project.html/public-project")
        self.assertRedirects(response, "/project.html?id=public-project", fetch_redirect_response=False)
        self.assert_untracked(response)
        for project_id in ("private-draft", "unknown-project"):
            response = self.client.get("/project.html/" + project_id)
            self.assertEqual(response.status_code, 404)
            self.assert_untracked(response)

    @patch("submissions.views.public_archive", side_effect=DatabaseError("unavailable"))
    def test_unavailable_project_lookup_disables_tracking_without_breaking_page(self, archive):
        response = self.client.get("/project.html?id=public-project")
        self.assertEqual(response.status_code, 200)
        self.assert_untracked(response)

    def test_privacy_notice_reflects_whether_tracking_is_configured(self):
        self.assertTrue(self.client.get("/privacy/").context["plausible_enabled"])
        with override_settings(PLAUSIBLE_SCRIPT_ID=""):
            self.assertFalse(self.client.get("/privacy/").context["plausible_enabled"])
