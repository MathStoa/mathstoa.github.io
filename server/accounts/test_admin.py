"""Private account identification stays verified, escaped and permission-bound."""
import time
from html.parser import HTMLParser

from allauth.account.internal.flows.login import record_authentication
from allauth.account.models import EmailAddress
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from submissions.models import Submission


class AccountListCells(HTMLParser):
    """Read visible admin columns without mistaking unrelated form fields for them."""

    def __init__(self, content):
        super().__init__()
        self.fields = {}
        self.current = None
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        if tag == "td":
            classes = dict(attrs).get("class", "").split()
            self.current = next((name for name in classes if name.startswith("field-")), None)
            if self.current:
                self.fields.setdefault(self.current, [])

    def handle_data(self, data):
        if self.current:
            self.fields[self.current].append(data)

    def handle_endtag(self, tag):
        if tag == "td":
            self.current = None

    def text(self, field):
        return " ".join(self.fields.get("field-" + field, []))


class AccountIdentificationAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.User = get_user_model()
        self.operator = self.User.objects.create_user(username="account-viewer", is_staff=True)
        self.view_permission = Permission.objects.get(codename="view_user", content_type__app_label="auth")
        self.operator.user_permissions.add(self.view_permission)
        self.author = self.User.objects.create_user(username="author-random-identifier")
        self.email = "verified-author@example.invalid"
        EmailAddress.objects.create(user=self.author, email=self.email, verified=True, primary=True)
        self.list_url = reverse("admin:auth_user_changelist")
        self.detail_url = reverse("admin:auth_user_change", args=[self.author.pk])

    def sign_in(self, user, *, mfa=True, expired=False):
        self.client.force_login(user)
        if mfa and user.is_staff:
            factor = TOTP.activate(user, generate_totp_secret()).instance
            request = RequestFactory().get("/")
            request.session = self.client.session
            record_authentication(request, user, "mfa", id=factor.pk, type="totp")
            if expired:
                request.session["account_authentication_methods"][-1]["at"] = (
                    time.time() - settings.STAFF_MFA_MAX_AGE - 60
                )
            request.session.save()

    def test_view_only_staff_can_identify_accounts_without_changing_them(self):
        secondary = "second-verified@example.invalid"
        unverified = "pending-verification@example.invalid"
        EmailAddress.objects.create(user=self.author, email=secondary, verified=True)
        EmailAddress.objects.create(user=self.author, email=unverified, verified=False)
        SocialAccount.objects.create(user=self.author, provider="orcid", uid="0000-0002-1825-0097")
        SocialAccount.objects.create(user=self.author, provider="github", uid="123456789")
        self.sign_in(self.operator)

        for url in (self.list_url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, self.email)
                self.assertContains(response, secondary)
                self.assertNotContains(response, unverified)
                self.assertContains(response, "ORCID")
                self.assertContains(response, "0000-0002-1825-0097")
                self.assertContains(response, 'href="https://orcid.org/0000-0002-1825-0097"')
                self.assertContains(response, "GitHub")
                self.assertContains(response, "123456789")
                self.assertIn("no-store", response["Cache-Control"])
                self.assertNotContains(response, 'name="verified_emails"')
                self.assertNotContains(response, 'name="connected_identities"')

        response = self.client.post(self.detail_url, {"email": "replacement@example.invalid"})
        self.assertEqual(response.status_code, 403)
        self.author.refresh_from_db()
        self.assertEqual(self.author.email, "")
        self.assertTrue(EmailAddress.objects.filter(user=self.author, email=self.email, verified=True).exists())

    def test_account_and_contributor_emails_are_not_assumed_verified(self):
        EmailAddress.objects.filter(user=self.author).delete()
        self.author.email = "unverified-account@example.invalid"
        self.author.save(update_fields=["email"])
        EmailAddress.objects.create(user=self.author, email=self.author.email, verified=False, primary=True)
        contributor_email = "someone-else@example.invalid"
        Submission.objects.create(
            owner=self.author,
            title="A project with another contributor",
            collaborators=[{"name": "Another, Person", "email": contributor_email}],
        )
        self.sign_in(self.operator)
        response = self.client.get(self.list_url, {"q": self.author.username})
        self.assertEqual(response.status_code, 200)
        cells = AccountListCells(response.content.decode())
        self.assertIn("No verified email", cells.text("verified_emails"))
        self.assertNotIn(self.author.email, cells.text("verified_emails"))
        self.assertIn("No connected identities", cells.text("connected_identities"))
        self.assertNotContains(response, contributor_email)

    def test_search_finds_related_emails_and_provider_ids_without_duplicate_rows(self):
        EmailAddress.objects.create(user=self.author, email="another-author@example.invalid", verified=True)
        SocialAccount.objects.create(user=self.author, provider="orcid", uid="0000-0002-1825-0097")
        SocialAccount.objects.create(user=self.author, provider="github", uid="987654321")
        self.User.objects.create_user(username="unrelated-account")
        self.sign_in(self.operator)

        for query in (self.email.upper(), "0000-0002-1825-0097", "987654321", "github", self.author.username, "author"):
            with self.subTest(query=query):
                response = self.client.get(self.list_url, {"q": query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual([user.pk for user in response.context["cl"].result_list], [self.author.pk])

    def test_connected_identity_is_escaped_in_list_and_detail(self):
        malicious_uid = '<img src=x onerror="alert(1)">'
        SocialAccount.objects.create(user=self.author, provider="github", uid=malicious_uid)
        self.sign_in(self.operator)
        for url in (self.list_url, self.detail_url):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, malicious_uid)
                self.assertContains(response, "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;")

    def test_account_identity_is_not_available_without_user_view_permission(self):
        ordinary = self.User.objects.create_user(username="ordinary-with-user-permission")
        ordinary.user_permissions.add(self.view_permission)
        staff = self.User.objects.create_user(username="staff-without-user-permission", is_staff=True)
        moderator = self.User.objects.create_user(username="moderator-without-user-permission", is_staff=True)
        moderator.user_permissions.add(Permission.objects.get(codename="change_submissionrevision"))

        for user in (None, ordinary, staff, moderator):
            self.client.logout()
            if user is not None:
                self.sign_in(user)
            for url in (self.list_url, self.detail_url):
                with self.subTest(user=user, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 302 if user is None else 403)
                    self.assertNotIn(self.email.encode(), response.content)

    def test_account_identity_requires_current_mfa_even_with_user_view_permission(self):
        self.sign_in(self.operator, mfa=False)
        for url in (self.list_url, self.detail_url):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn(self.email.encode(), response.content)

        self.sign_in(self.operator, expired=True)
        for url in (self.list_url, self.detail_url):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse("mfa_reauthenticate")))
            self.assertNotIn(self.email.encode(), response.content)
