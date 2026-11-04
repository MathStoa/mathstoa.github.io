"""One permission-aware menu across static content, accounts and administration."""
from html.parser import HTMLParser

from allauth.account.internal.flows.login import record_authentication
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.views.defaults import permission_denied

from submissions.models import Submission, SubmissionRevision

from .views import csrf_failure


class MainNavigation(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inside = False
        self.links = []
        self.forms = []
        self.csrf_tokens = []
        self.buttons = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav" and attrs.get("aria-label") == "Main navigation":
            self.inside = True
        if not self.inside:
            return
        if tag == "a":
            self.links.append(attrs.get("href"))
        if tag == "form":
            self.forms.append(attrs)
        if tag == "input" and attrs.get("name") == "csrfmiddlewaretoken":
            self.csrf_tokens.append(attrs.get("value"))
        if tag == "button":
            self.buttons.append(attrs)

    def handle_endtag(self, tag):
        if tag == "nav":
            self.inside = False


class NavigationFixtures:
    public_paths = ("/", "/index.html", "/about.html", "/project.html?id=example", "/404.html", "/privacy/", "/missing-page")

    def create_users(self):
        cache.clear()
        User = get_user_model()
        self.users = {
            "ordinary": User.objects.create_user(username="nav-reader"),
            # A permission alone never grants staff navigation or access.
            "ordinary_with_permission": User.objects.create_user(username="nav-nonstaff-permission"),
            "staff": User.objects.create_user(username="nav-staff", is_staff=True),
            "moderator": User.objects.create_user(username="nav-moderator", is_staff=True),
            "privacy_operator": User.objects.create_user(username="nav-privacy", is_staff=True),
            "admin": User.objects.create_superuser(username="nav-admin", email="nav@example.invalid", password=None),
        }
        moderation = Permission.objects.get(codename="change_submissionrevision")
        for role in ("moderator", "ordinary_with_permission"):
            self.users[role].user_permissions.add(moderation)
        self.users["privacy_operator"].user_permissions.add(Permission.objects.get(codename="view_user"))
        self.submissions = {role: Submission.objects.create(owner=user, title="Navigation test", repository_url="https://github.com/example/navigation") for role, user in self.users.items()}
        submission = self.submissions["ordinary"]
        SubmissionRevision.objects.create(submission=submission, number=1, snapshot=submission.snapshot(), submitted_by=submission.owner)

    def authenticate(self, client, role, *, mfa=True):
        client.force_login(self.users[role])
        if mfa and self.users[role].is_staff:
            factor = TOTP.activate(self.users[role], generate_totp_secret()).instance
            request = RequestFactory().get("/")
            request.session = client.session
            record_authentication(request, self.users[role], "mfa", id=factor.pk, type="totp")
            request.session.save()

    def private_paths(self, role):
        submission = self.submissions[role]
        return (
            "/submissions/", "/submissions/new/", f"/submissions/{submission.pk}/",
            f"/submissions/{submission.pk}/edit/", f"/verify/{submission.pk}/",
            reverse("socialaccount_connections"), reverse("account_email"),
            reverse("account_privacy"), reverse("account_logout"), reverse("mfa_index"),
        )

    def expected_links(self, role):
        links = ["/index.html#archive", "/about.html"]
        if role == "anonymous":
            return links + [reverse("account_login"), reverse("submissions:create")]
        links += [reverse("submissions:list"), reverse("socialaccount_connections")]
        if self.users[role].is_staff:
            links.append(reverse("admin:index"))
        if role in {"privacy_operator", "admin"}:
            links.append(reverse("privacy_request_verify"))
        if role in {"moderator", "admin"}:
            links.append(reverse("admin:submissions_submissionrevision_changelist") + "?status__exact=pending")
        return links + [reverse("submissions:create")]


@override_settings(DEBUG=False)
class NavigationTests(NavigationFixtures, TestCase):
    def setUp(self):
        self.create_users()

    def assert_menu(self, response, role):
        self.assertIn(response.status_code, (200, 403, 404))
        nav = MainNavigation(response.content.decode())
        self.assertEqual(nav.links, self.expected_links(role))
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("Cookie", response.get("Vary", ""))
        policy = response["Content-Security-Policy"]
        self.assertIn("script-src 'self';", policy)
        self.assertIn("style-src 'self';", policy)
        self.assertIn("form-action 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)
        if role == "anonymous":
            self.assertEqual(nav.forms, [])
        else:
            self.assertEqual(len(nav.forms), 1)
            self.assertEqual(nav.forms[0]["method"], "post")
            self.assertEqual(nav.forms[0]["action"], reverse("account_logout"))
            self.assertEqual(nav.buttons[0]["type"], "submit")
            self.assertEqual(len(nav.csrf_tokens), 1)
            self.assertTrue(nav.csrf_tokens[0])
        return nav

    def test_role_matrix_on_public_and_backend_pages(self):
        for role in ("anonymous", *self.users):
            self.client.logout()
            if role != "anonymous":
                self.authenticate(self.client, role)
            paths = list(self.public_paths)
            if role == "anonymous":
                paths += [reverse("account_login"), reverse("account_signup")]
            else:
                paths += list(self.private_paths(role))
                if self.users[role].is_staff:
                    paths += [reverse("admin:index")]
                if role in {"moderator", "admin"}:
                    paths += [reverse("admin:submissions_submissionrevision_changelist")]
                if role in {"privacy_operator", "admin"}:
                    paths += [reverse("privacy_request_verify")]
            for path in paths:
                with self.subTest(role=role, path=path):
                    response = self.client.get(path)
                    self.assert_menu(response, role)
                    if role in {"moderator", "admin"}:
                        self.assertContains(response, "Moderation queue (1)", status_code=response.status_code)

    def test_permissions_still_enforced_for_direct_admin_requests(self):
        admin_paths = (reverse("admin:index"), reverse("admin:submissions_submissionrevision_changelist"), reverse("privacy_request_verify"))
        for path in admin_paths:
            self.assertEqual(self.client.get(path).status_code, 302)
        for role in ("ordinary", "ordinary_with_permission"):
            self.authenticate(self.client, role)
            for path in admin_paths:
                with self.subTest(role=role, path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 403)
                    self.assert_menu(response, role)
        self.authenticate(self.client, "staff")
        for path in admin_paths[1:]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 403)
            self.assert_menu(response, "staff")
        self.authenticate(self.client, "moderator")
        response = self.client.get(reverse("privacy_request_verify"))
        self.assertEqual(response.status_code, 403)
        self.assert_menu(response, "moderator")

    def test_admin_navigation_does_not_bypass_mfa(self):
        self.authenticate(self.client, "admin", mfa=False)
        self.assert_menu(self.client.get(reverse("admin:index")), "admin")
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 403)
        factor = TOTP.activate(self.users["admin"], generate_totp_secret()).instance
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("mfa_reauthenticate")))
        factor.delete()

    def test_each_public_logout_form_requires_its_own_valid_csrf_post(self):
        for path in self.public_paths:
            with self.subTest(path=path):
                client = Client(enforce_csrf_checks=True)
                self.authenticate(client, "ordinary")
                response = client.get(path)
                nav = self.assert_menu(response, "ordinary")
                self.assertNotIn("<base ", response.content.decode())
                logout = nav.forms[0]["action"]
                client.get(logout)
                self.assertIn("_auth_user_id", client.session)
                rejected = client.post(logout)
                self.assertEqual(rejected.status_code, 403)
                self.assert_menu(rejected, "ordinary")
                self.assertIn("_auth_user_id", client.session)
                self.assertEqual(client.post(logout, {"csrfmiddlewaretoken": nav.csrf_tokens[0]}, HTTP_ORIGIN="https://other.invalid").status_code, 403)
                self.assertEqual(client.post(logout, {"csrfmiddlewaretoken": nav.csrf_tokens[0]}).status_code, 302)
                self.assertNotIn("_auth_user_id", client.session)
                self.assert_menu(client.get(path), "anonymous")

    def test_role_changes_do_not_reuse_previous_navigation(self):
        self.authenticate(self.client, "admin")
        self.assert_menu(self.client.get("/"), "admin")
        user = self.users["admin"]
        user.is_staff = False
        user.is_superuser = False
        user.save()
        self.assert_menu(self.client.get("/"), "ordinary")
        self.client.logout()
        self.assert_menu(self.client.get("/"), "anonymous")

    def test_public_queue_count_and_permission_changes_are_rechecked(self):
        self.authenticate(self.client, "moderator")
        self.assertContains(self.client.get("/about.html"), "Moderation queue (1)")
        SubmissionRevision.objects.update(status="rejected")
        response = self.client.get("/about.html")
        self.assertContains(response, "Moderation queue</a>")
        self.assertNotContains(response, "Moderation queue (")
        self.users["moderator"].user_permissions.clear()
        self.assert_menu(self.client.get("/about.html"), "staff")

    def test_admin_menu_can_sign_out_with_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.authenticate(client, "admin")
        nav = self.assert_menu(client.get(reverse("admin:index")), "admin")
        self.assertEqual(client.post(reverse("account_logout")).status_code, 403)
        response = client.post(reverse("account_logout"), {"csrfmiddlewaretoken": nav.csrf_tokens[0]})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", client.session)
        self.assert_menu(client.get("/"), "anonymous")

    def test_csrf_failure_navigation_preserves_the_signed_in_role_and_rejection(self):
        for role in ("anonymous", "ordinary", "staff", "moderator", "admin"):
            with self.subTest(role=role):
                client = Client(enforce_csrf_checks=True)
                if role != "anonymous":
                    self.authenticate(client, role)
                response = client.post(reverse("account_logout"), {"csrfmiddlewaretoken": "untrusted-token"})
                self.assertEqual(response.status_code, 403)
                self.assert_menu(response, role)
                self.assertContains(response, "Your action was not processed", status_code=403)
                self.assertNotContains(response, "untrusted-token", status_code=403)
                if role != "anonymous":
                    self.assertIn("_auth_user_id", client.session)
                    nav = MainNavigation(response.content.decode())
                    self.assertEqual(client.post(reverse("account_logout"), {"csrfmiddlewaretoken": nav.csrf_tokens[0]}).status_code, 302)
                    self.assertNotIn("_auth_user_id", client.session)

    def test_forbidden_templates_do_not_echo_exception_or_csrf_details(self):
        request = RequestFactory().get("/")
        request.user = self.users["ordinary"]
        sensitive = "private-request-details-<script>untrusted()</script>"
        for response in (permission_denied(request, Exception(sensitive)), csrf_failure(request, reason=sensitive)):
            with self.subTest(template=response.content[:30]):
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("private-request-details", response.content.decode())
                self.assertEqual(MainNavigation(response.content.decode()).links, self.expected_links("ordinary"))
