"""Opt-in browser regression checks against the real Django service.

MATHSTOA_BROWSER_TESTS=1 python server/manage.py test accounts.test_navigation_browser \
    --settings=config.test_settings
Install playwright==1.60.0 and Chromium first. MATHSTOA_BROWSER_EXECUTABLE may
point to an existing Chrome executable instead.
"""
import os
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.servers.basehttp import WSGIServer
from django.test import Client, override_settings
from django.test.testcases import LiveServerThread, QuietWSGIRequestHandler

from .test_navigation import NavigationFixtures


class NavigationServerThread(LiveServerThread):
    def _create_server(self, connections_override=None):
        # SQLite's in-memory test database shares a connection across threads.
        # Serve browser asset requests serially to avoid concurrent use of it.
        return WSGIServer(
            (self.host, self.port), QuietWSGIRequestHandler,
            allow_reuse_address=False,
        )


@skipUnless(os.environ.get("MATHSTOA_BROWSER_TESTS") == "1", "Set MATHSTOA_BROWSER_TESTS=1 to run Chromium navigation checks")
@override_settings(DEBUG=False)
class NavigationBrowserTests(NavigationFixtures, StaticLiveServerTestCase):
    server_thread_class = NavigationServerThread

    def setUp(self):
        # Prepare all database state before Playwright starts its event loop.
        # Stop it in cleanups before Django flushes this isolated test database.
        self.create_users()
        self.sessions = {}
        for role in self.users:
            client = Client()
            self.authenticate(client, role)
            self.sessions[role] = client.cookies[settings.SESSION_COOKIE_NAME].value
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        options = {"headless": True}
        if os.environ.get("MATHSTOA_BROWSER_EXECUTABLE"):
            options["executable_path"] = os.environ["MATHSTOA_BROWSER_EXECUTABLE"]
        self.browser = self.playwright.chromium.launch(**options)
        self.addCleanup(self.browser.close)

    def context_for(self, role):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        context.add_init_script("""
            window.navigationCspViolations = [];
            document.addEventListener('securitypolicyviolation', event => {
                window.navigationCspViolations.push({directive: event.violatedDirective, blocked: event.blockedURI});
            });
        """)
        if role != "anonymous":
            context.add_cookies([{
                "name": settings.SESSION_COOKIE_NAME,
                "value": self.sessions[role],
                "url": self.live_server_url,
            }])
        return context

    def assert_visible_menu(self, page, role):
        self.assertEqual(page.evaluate("window.navigationCspViolations"), [], page.url)
        nav = page.get_by_role("navigation", name="Main navigation", exact=True)
        self.assertEqual(nav.count(), 1)
        self.assertEqual(nav.locator("a").evaluate_all("nodes => nodes.map(n => n.getAttribute('href'))"), self.expected_links(role), page.url)
        account = nav.locator("details.account-menu")
        if role == "anonymous":
            self.assertEqual(account.count(), 0)
        else:
            self.assertEqual(account.count(), 1)
            summary = account.locator("summary")
            self.assertTrue(summary.is_visible())
            if account.get_attribute("open") is None:
                self.assertFalse(account.get_by_role("link", name="Account settings", exact=True).is_visible())
                summary.click()
            self.assertIsNotNone(account.get_attribute("open"))
        items = nav.locator("a,button,summary")
        viewport = page.viewport_size["width"]
        for item in items.all():
            self.assertTrue(item.is_visible(), item.text_content())
            box = item.bounding_box()
            self.assertGreaterEqual(box["x"], 0, item.text_content())
            self.assertLessEqual(box["x"] + box["width"], viewport + 1, item.text_content())
            self.assertGreater(box["width"], 0)
        # Controls must not overlap one another. The dropdown may overlay the
        # page body, but the header itself must stay above the main content.
        boxes = items.evaluate_all("nodes => nodes.map(n => {const r=n.getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom};})")
        for i, first in enumerate(boxes):
            for second in boxes[i+1:]:
                overlap_x = min(first["right"], second["right"]) - max(first["x"], second["x"])
                overlap_y = min(first["bottom"], second["bottom"]) - max(first["y"], second["y"])
                self.assertFalse(overlap_x > 1 and overlap_y > 1, "Navigation items overlap")
        header = page.locator("header").first.bounding_box()
        main = page.locator("main").first.bounding_box()
        self.assertLessEqual(header["y"] + header["height"], main["y"] + 1)

    def test_all_roles_and_pages_at_mobile_tablet_and_desktop_widths(self):
        screenshots = os.environ.get("MATHSTOA_NAV_SCREENSHOTS")
        for role in ("anonymous", *self.users):
            context = self.context_for(role)
            page = context.new_page()
            paths = list(self.public_paths)
            if role == "anonymous":
                paths += ["/accounts/login/"]
            else:
                paths += list(self.private_paths(role))
                paths += ["/admin/", "/admin/submissions/submissionrevision/", "/admin/privacy-requests/verify/"]
            for width in (320, 375, 768, 1024, 1280):
                page.set_viewport_size({"width": width, "height": 1000})
                for path in paths:
                    with self.subTest(role=role, width=width, path=path):
                        response = page.goto(self.live_server_url + path)
                        self.assertIn(response.status, (200, 403, 404))
                        self.assert_visible_menu(page, role)
                        self.assertIn("no-store", response.headers["cache-control"])
                        if screenshots and role == "admin" and path in ("/about.html", "/admin/") and width in (375, 1280):
                            directory = Path(screenshots)
                            directory.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(directory / f"{role}-{width}-{'admin' if path == '/admin/' else 'about'}.png"))
            context.close()

    def test_navigation_between_static_backend_and_admin_then_sign_out(self):
        for role in ("ordinary", "admin"):
            context = self.context_for(role)
            page = context.new_page()
            page.set_viewport_size({"width": 375, "height": 900})
            page.goto(self.live_server_url + "/project.html?id=example")
            nav = page.get_by_role("navigation", name="Main navigation", exact=True)
            self.assert_visible_menu(page, role)
            nav.get_by_role("link", name="Account settings", exact=True).click()
            self.assertTrue(page.url.endswith("/accounts/3rdparty/"))
            self.assert_visible_menu(page, role)
            if role == "admin":
                nav.get_by_role("link", name="Administration", exact=True).click()
                self.assertTrue(page.url.endswith("/admin/"))
                self.assert_visible_menu(page, role)
            nav.get_by_role("link", name="About", exact=True).click()
            self.assert_visible_menu(page, role)
            nav.get_by_role("button", name="Sign out", exact=True).click()
            self.assert_visible_menu(page, "anonymous")
            page.goto(self.live_server_url + "/project.html?id=example")
            self.assert_visible_menu(page, "anonymous")
            context.close()

    def assert_keyboard_menu(self, page, role):
        nav = page.get_by_role("navigation", name="Main navigation", exact=True)
        items = nav.locator("a,button,summary")
        # Enter the menu with Tab, then visit every item in document order.
        # Do not use locator.focus(), which would miss broken tab order.
        for _ in range(15):
            page.keyboard.press("Tab")
            if items.first.evaluate("node => node === document.activeElement"):
                break
        self.assertTrue(items.first.evaluate("node => node === document.activeElement"))
        for index, item in enumerate(items.all()):
            if index:
                page.keyboard.press("Tab")
            self.assertTrue(item.evaluate("node => node === document.activeElement"), item.text_content())
            self.assertTrue(item.inner_text().strip(), "Navigation control needs an accessible name")
            self.assertTrue(item.evaluate("""node => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return node.matches(':focus-visible') && style.outlineStyle !== 'none'
                    && parseFloat(style.outlineWidth) >= 2 && parseFloat(style.outlineOffset) >= 2
                    && rect.left >= 0 && rect.right <= innerWidth + 1;
            }"""), item.text_content())
            if item.evaluate("node => node.tagName === 'SUMMARY'"):
                # Native disclosure stays keyboard reachable without scripts;
                # opening it puts the permission-aware links into tab order.
                page.keyboard.press("Enter")
                self.assertIsNotNone(nav.locator("details.account-menu").get_attribute("open"))
        if role != "anonymous":
            account = nav.locator("details.account-menu")
            summary = account.locator("summary")
            for _ in range(items.count()):
                page.keyboard.press("Shift+Tab")
                if summary.evaluate("node => node === document.activeElement"):
                    break
            self.assertTrue(summary.evaluate("node => node === document.activeElement"))
            page.keyboard.press("Space")
            self.assertIsNone(account.get_attribute("open"))
            self.assertFalse(account.get_by_role("button", name="Sign out", exact=True).is_visible())
            # A closed account menu must not leave invisible links focusable.
            page.keyboard.press("Tab")
            submit = nav.get_by_role("link", name="Submit a project", exact=True)
            self.assertTrue(submit.evaluate("node => node === document.activeElement"))
            page.keyboard.press("Shift+Tab")
            page.keyboard.press("Enter")
            self.assertIsNotNone(account.get_attribute("open"))
        self.assert_visible_menu(page, role)

    def test_keyboard_focus_skip_links_and_csrf_error_recovery(self):
        for role in ("anonymous", "ordinary", "staff", "moderator", "admin"):
            context = self.context_for(role)
            page = context.new_page()
            for width in (375, 1280):
                page.set_viewport_size({"width": width, "height": 900})
                paths = ["/about.html", "/privacy/"]
                if role != "anonymous":
                    paths += ["/admin/", "/admin/submissions/submissionrevision/", "/admin/privacy-requests/verify/"]
                for path in paths:
                    with self.subTest(role=role, width=width, path=path):
                        page.goto(self.live_server_url + path)
                        self.assert_keyboard_menu(page, role)
                        # The skip link must move focus to the content without
                        # adding the main element to the ordinary tab sequence.
                        page.goto(self.live_server_url + path)
                        page.keyboard.press("Tab")
                        self.assertIn(page.locator(":focus").inner_text(), ("Skip to content", "Skip to main content"))
                        page.keyboard.press("Enter")
                        self.assertEqual(page.locator(":focus").get_attribute("tabindex"), "-1")
                # Submit an intentionally invalid token through an actual
                # same-origin browser form, so the real CSRF middleware runs.
                page.goto(self.live_server_url + "/privacy/")
                page.evaluate("""() => {
                    const form = document.createElement('form');
                    form.method = 'post'; form.action = '/accounts/logout/';
                    document.body.append(form); form.submit();
                }""")
                page.wait_for_url(self.live_server_url + "/accounts/logout/")
                page.get_by_role("heading", name="Request could not be verified", exact=True).wait_for()
                self.assert_keyboard_menu(page, role)
                if role != "anonymous":
                    self.assertEqual(page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role("button", name="Sign out", exact=True).count(), 1)
            if role != "anonymous":
                page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role("button", name="Sign out", exact=True).click()
                self.assert_visible_menu(page, "anonymous")
            context.close()
