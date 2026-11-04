"""The real revision form submits comments with each decision under CSRF/MFA."""
import os
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from accounts.test_navigation_browser import NavigationServerThread
from .models import NotificationDelivery
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
