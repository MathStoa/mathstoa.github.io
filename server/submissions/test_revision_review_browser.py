"""The real revision form submits comments with each decision under CSRF/MFA."""
import os
import json
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from accounts.test_navigation_browser import NavigationServerThread
from .models import NotificationDelivery, SubmissionRevision
from .test_revision_review import IndividualReviewFixtures


@skipUnless(os.environ.get("MATHSTOA_BROWSER_TESTS") == "1", "Set MATHSTOA_BROWSER_TESTS=1 to run Chromium review checks")
@override_settings(DEBUG=False, DEFAULT_FROM_EMAIL="notice@example.invalid")
class IndividualReviewBrowserTests(IndividualReviewFixtures, StaticLiveServerTestCase):
    server_thread_class = NavigationServerThread

    def test_comments_survive_failed_approval_then_submit_with_request_changes(self):
        self.login()
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            options = {"headless": True}
            if os.environ.get("MATHSTOA_BROWSER_EXECUTABLE"):
                options["executable_path"] = os.environ["MATHSTOA_BROWSER_EXECUTABLE"]
            browser = playwright.chromium.launch(**options)
            try:
                context = browser.new_context()
                context.add_cookies([{"name": settings.SESSION_COOKIE_NAME, "value": cookie, "url": self.live_server_url}])
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for width in (375, 1280):
                    page.set_viewport_size({"width": width, "height": 1000})
                    page.goto(self.live_server_url + self.url)
                    for label in ("Save", "Save and continue editing", "Approve", "Request changes"):
                        control = page.get_by_role("button", name=label, exact=True)
                        self.assertTrue(control.is_visible(), label)
                        box = control.bounding_box()
                        self.assertGreaterEqual(box["x"], 0)
                        self.assertLessEqual(box["x"] + box["width"], width + 1)
                note = "Please clarify the proof. <script>privateFeedback()</script>"
                page.locator("#id_review_note").fill(note)
                page.get_by_role("button", name="Approve", exact=True).click()
                page.locator(".errornote").wait_for()
                self.assertEqual(page.locator("#id_review_note").input_value(), note)
                self.assertEqual(page.locator("script").filter(has_text="privateFeedback").count(), 0)
                screenshots = os.environ.get("MATHSTOA_REVIEW_SCREENSHOTS")
                if screenshots:
                    directory = Path(screenshots)
                    directory.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(directory / "review-failed-approval.png"), full_page=True)
                page.get_by_role("button", name="Request changes", exact=True).click()
                page.wait_for_url(self.live_server_url + self.queue)
                page.goto(self.live_server_url + self.url)
                self.assertEqual(page.get_by_role("button", name="Approve", exact=True).count(), 0)
                self.assertEqual(page.get_by_role("button", name="Request changes", exact=True).count(), 0)
                self.assertIn(note, page.locator(".field-review_note").text_content())
                self.assertEqual(errors, [])
            finally:
                browser.close()
        self.revision.refresh_from_db()
        self.assertEqual(self.revision.status, "rejected")
        self.assertEqual(self.revision.review_note, note)
        self.assertEqual(NotificationDelivery.objects.count(), 1)


@skipUnless(os.environ.get("MATHSTOA_BROWSER_TESTS") == "1", "Set MATHSTOA_BROWSER_TESTS=1 to run Chromium review checks")
@override_settings(DEBUG=False)
class SubmittedDetailsBrowserTests(IndividualReviewFixtures, StaticLiveServerTestCase):
    server_thread_class = NavigationServerThread

    def setUp(self):
        super().setUp()
        self.login()
        self.snapshot = {
            **self.revision.snapshot,
            "title": "Random walks and spectral limits",
            "abstract": "We study $x^2$ and the limit of discrete random walks.\n\nThe current notes develop the estimate\n$$" + "+".join(["x_i^2"] * 50) + "$$\nand describe open questions. Unsupported notation stays as source: $\\mathbb{R}$.",
            "category": "math.PR",
            "secondary_categories": ["math.AP", "math.FA"],
            "collaborators": [
                {"name": "Ada,  Example", "email": "ada@example.invalid", "orcid": "https://orcid.org/0000-0002-1825-0097"},
                {"name": "Émilie, Example", "email": "long" * 40 + "@example.invalid", "orcid": "0000-0002-1825-0097"},
                {"name": "李, Example"},
            ],
            "legacy_field": {"source": "$x$ <uninterpreted> & exact", "flag": False},
        }
        self.submission.title = "Later draft title"
        self.submission.abstract = "Later draft abstract"
        self.submission.save()

    def open_review(self, *, javascript=True, color_scheme="light"):
        # Prepare database state before Playwright starts its event loop.
        SubmissionRevision.objects.filter(pk=self.revision.pk).update(snapshot=self.snapshot)
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        from playwright.sync_api import sync_playwright
        playwright = sync_playwright().start()
        self.addCleanup(playwright.stop)
        options = {"headless": True}
        if os.environ.get("MATHSTOA_BROWSER_EXECUTABLE"):
            options["executable_path"] = os.environ["MATHSTOA_BROWSER_EXECUTABLE"]
        browser = playwright.chromium.launch(**options)
        self.addCleanup(browser.close)
        context = browser.new_context(java_script_enabled=javascript, color_scheme=color_scheme)
        context.add_cookies([{"name": settings.SESSION_COOKIE_NAME, "value": cookie, "url": self.live_server_url}])
        context.add_init_script("""
            window.reviewCspViolations = [];
            document.addEventListener('securitypolicyviolation', event => window.reviewCspViolations.push(event.violatedDirective));
        """)
        page = context.new_page()
        self.errors = []
        self.external_requests = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("request", lambda request: self.external_requests.append(request.url) if not request.url.startswith(self.live_server_url + "/") else None)
        page.goto(self.live_server_url + self.url)
        return page

    def assert_fits_viewport(self, page):
        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), page.viewport_size["width"] + 1)
        for item in page.locator(".revision-review section, .revision-review-json, .revision-review-contributors > li").all():
            box = item.bounding_box()
            self.assertGreaterEqual(box["x"], 0)
            self.assertLessEqual(box["x"] + box["width"], page.viewport_size["width"] + 1)

    def test_summary_math_mobile_layout_and_native_keyboard_disclosure(self):
        page = self.open_review()
        raw = page.locator(".revision-review-json pre")
        disclosure = page.locator(".revision-review-json")
        summary = disclosure.locator("summary")
        for width in (320, 375, 768, 1280):
            page.set_viewport_size({"width": width, "height": 1000})
            page.goto(self.live_server_url + self.url)
            page.locator(".revision-review math").first.wait_for()
            self.assertEqual(page.locator(".revision-review math").count(), 2)
            self.assertEqual(page.locator(".revision-review math[display='block']").count(), 1)
            self.assertIn(r"$\mathbb{R}$", page.locator("[data-abstract]").text_content())
            self.assertEqual(page.locator(".revision-review-title").text_content().strip(), self.snapshot["title"])
            self.assertEqual(page.locator(".revision-review-name").all_text_contents(), [person["name"] + "\n" for person in self.snapshot["collaborators"]])
            self.assertNotIn("Later draft", page.locator(".revision-review").text_content())
            self.assertFalse(raw.is_visible())
            self.assertIsNone(disclosure.get_attribute("open"))
            self.assert_fits_viewport(page)
            for label in ("Title", "Abstract", "Categories", "Contributors (3)", "Project links"):
                self.assertTrue(page.get_by_role("region", name=label, exact=True).is_visible())
            for label in ("Save", "Save and continue editing", "Approve", "Request changes"):
                self.assertTrue(page.get_by_role("button", name=label, exact=True).is_visible())
            summary.focus()
            summary.press("Enter")
            self.assertIsNotNone(disclosure.get_attribute("open"))
            self.assertTrue(raw.is_visible())
            self.assertEqual(json.loads(raw.text_content()), self.snapshot)
            self.assertEqual(raw.locator("math").count(), 0)
            self.assert_fits_viewport(page)
            summary.press("Space")
            self.assertIsNone(disclosure.get_attribute("open"))
            self.assertFalse(raw.is_visible())
            screenshots = os.environ.get("MATHSTOA_REVIEW_SCREENSHOTS")
            if screenshots and width in (375, 1280):
                directory = Path(screenshots)
                directory.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(directory / f"submitted-details-{width}.png"), full_page=True)
        page.emulate_media(color_scheme="dark")
        self.assert_fits_viewport(page)
        if screenshots:
            page.screenshot(path=str(directory / "submitted-details-dark.png"), full_page=True)
            summary.click()
            self.assertTrue(raw.is_visible())
            self.assert_fits_viewport(page)
            page.screenshot(path=str(directory / "submitted-details-json-expanded.png"), full_page=True)
        self.assertEqual(page.evaluate("window.reviewCspViolations"), [])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external_requests, [])

    def test_source_and_disclosure_work_without_javascript(self):
        page = self.open_review(javascript=False)
        page.set_viewport_size({"width": 320, "height": 1000})
        self.assertEqual(page.locator("[data-abstract]").text_content(), self.snapshot["abstract"])
        self.assertEqual(page.locator("math").count(), 0)
        self.assertFalse(page.locator(".revision-review-json pre").is_visible())
        page.locator(".revision-review-json summary").click()
        self.assertTrue(page.locator(".revision-review-json pre").is_visible())
        self.assertEqual(json.loads(page.locator(".revision-review-json pre").text_content()), self.snapshot)
        self.assert_fits_viewport(page)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external_requests, [])

    def test_hostile_text_and_links_cannot_create_active_content(self):
        hostile = '<img src="https://evil.example/x" onerror="window.reviewXss=1"><script>window.reviewXss=2</script></pre>'
        self.snapshot.update(title=hostile, abstract=hostile + r" $\href{javascript:alert(1)}{bad}$", category=hostile, secondary_categories=[hostile], repository_url="javascript:alert(1)", reading_url='https://example.org/" onclick="window.reviewXss=3', doi="data:text/html,evil", collaborators=[{"name": hostile, "email": hostile, "orcid": "javascript:alert(1)"}])
        page = self.open_review()
        page.wait_for_function("document.querySelector('[data-abstract]').classList.contains('abstract-text')")
        page.locator(".revision-review-json summary").click()
        self.assertEqual(page.locator(".revision-review img, .revision-review script, .revision-review svg, .revision-review a, .revision-review [onclick], .revision-review [onerror], .revision-review [style]").count(), 0)
        self.assertIn(hostile, page.locator("[data-abstract]").text_content())
        self.assertEqual(json.loads(page.locator(".revision-review-json pre").text_content()), self.snapshot)
        self.assertIsNone(page.evaluate("window.reviewXss"))
        self.assertEqual(page.evaluate("window.reviewCspViolations"), [])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external_requests, [])
