"""Organisation entry points and permission-aware account navigation."""
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import OrganisationAccount


class OrganisationUITests(TestCase):
    def setUp(self):
        cache.clear()

    def organisation(self, *, status="pending", name="Example Research Group"):
        user = get_user_model().objects.create_user(
            username="organisation-ui", email="group@example.org", is_active=status == "approved",
        )
        EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=status == "approved")
        return OrganisationAccount.objects.create(
            user=user, name=name, email=user.email, website="https://example.org",
            description="A mathematical research group.", representative_name="A Representative",
            authority="I am authorised to administer the organisation account.", status=status,
        )

    @override_settings(SOCIALACCOUNT_PROVIDERS={
        "orcid": {"APPS": [{"client_id": "test-orcid", "secret": "test-orcid-secret"}]},
        "github": {"APPS": [{"client_id": "test-github", "secret": "test-github-secret"}]},
        "gitlab": {"APPS": [{"client_id": "test-gitlab", "secret": "test-gitlab-secret"}]},
    })
    def test_organisation_follows_the_three_provider_options(self):
        response = self.client.get(reverse("account_login"))
        self.assertContains(response, 'href="' + reverse("organisation_login") + '">Organisation</a>')
        html = response.content.decode()
        organisation_position = html.index('href="' + reverse("organisation_login") + '"')
        for provider in ("orcid", "github", "gitlab"):
            self.assertLess(html.index(f'action="/accounts/{provider}/login/'), organisation_position)

    def test_organisation_landing_links_to_registration_with_moderation_explanation(self):
        response = self.client.get(reverse("organisation_login"))
        self.assertContains(response, "Sign in as an organisation")
        self.assertContains(response, 'action="' + reverse("organisation_login") + '"')
        self.assertContains(response, 'href="' + reverse("organisation_register") + '"')
        self.assertContains(response, "approve your registration before the organisation account is activated")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    @override_settings(PUBLIC_REGISTRATION_ENABLED=False)
    def test_closed_registration_keeps_approved_sign_in_available(self):
        response = self.client.get(reverse("organisation_login"))
        self.assertContains(response, "Send sign-in code")
        self.assertContains(response, "New organisation registration is currently closed")
        self.assertNotContains(response, 'href="' + reverse("organisation_register") + '"')

    @override_settings(ORGANISATION_LOGIN_ENABLED=False)
    def test_disabled_email_has_explanatory_landing(self):
        response = self.client.get(reverse("organisation_login"))
        self.assertContains(response, "Organisation sign-in and registration are currently unavailable")
        self.assertNotContains(response, "Send sign-in code")
        self.assertNotContains(response, 'href="' + reverse("organisation_register") + '"')

    def test_moderation_queue_count_requires_active_staff_and_permission(self):
        self.organisation()
        permission = Permission.objects.get(codename="change_organisationaccount")
        queue_url = reverse("admin:accounts_organisationaccount_changelist") + "?status__exact=pending"
        self.assertNotContains(self.client.get("/"), queue_url)
        for role, is_staff, allowed in (("reader", False, True), ("staff", True, False), ("reviewer", True, True)):
            with self.subTest(role=role):
                user = get_user_model().objects.create_user(username="org-ui-" + role, is_staff=is_staff)
                if allowed:
                    user.user_permissions.add(permission)
                self.client.force_login(user)
                response = self.client.get("/")
                if is_staff and allowed:
                    self.assertContains(response, queue_url)
                    self.assertContains(response, "Organisation applications (1)")
                else:
                    self.assertNotContains(response, queue_url)
                self.client.logout()

    def test_approved_organisation_identity_is_escaped_and_uses_organisation_settings(self):
        organisation = self.organisation(status="approved", name="Research <script>alert(1)</script>")
        self.client.force_login(organisation.user)
        response = self.client.get(reverse("socialaccount_connections"))
        self.assertContains(response, "Your organisation account")
        self.assertContains(response, "Research &lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "Research <script>")
        self.assertContains(response, organisation.email)
        self.assertContains(response, "Set up or manage your authenticator")
        self.assertContains(response, "Your data and privacy requests")
        self.assertNotContains(response, "Connect another sign-in method")
        self.assertNotContains(response, "Manage your private email address")

    def test_application_queue_count_is_rechecked(self):
        organisation = self.organisation()
        user = get_user_model().objects.create_superuser(username="org-ui-admin", email="admin@example.org", password=None)
        self.client.force_login(user)
        self.assertContains(self.client.get("/"), "Organisation applications (1)")
        OrganisationAccount.objects.filter(pk=organisation.pk).update(status="rejected")
        response = self.client.get("/")
        self.assertContains(response, "Organisation applications</a>")
        self.assertNotContains(response, "Organisation applications (")
