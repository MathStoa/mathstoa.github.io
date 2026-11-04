"""Privacy proofs bind exact requests; downloads never cross account boundaries."""
import copy
import json
import time
from datetime import timedelta
from unittest.mock import patch

from allauth.account.internal.flows.login import record_authentication
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, generate_totp_secret, hotp_value
from allauth.socialaccount.models import SocialAccount
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.core import mail, signing
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.views.debug import SafeExceptionReporterFilter

from repository_verification.models import RepositoryVerification, VerificationAttempt
from submissions.models import Submission, SubmissionRevision
from .privacy_services import (
    REQUEST_MAX_AGE, REQUEST_SIGNING_SALT, PrivacyRequestError,
    account_export, authentication_version, create_request_bundle, verify_request_bundle,
)

User = get_user_model()


@override_settings(PUBLIC_CONTACT_EMAIL="privacy@example.org", EMAIL_LOGIN_ENABLED=False)
class PrivacyToolsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="requester")
        self.identity = SocialAccount.objects.create(user=self.user, provider="github", uid="requester-id")
        self.other = User.objects.create_user(username="other-author", email="other-private@example.org")
        self.client.force_login(self.user)

    def record(self, method="socialaccount", *, user=None, at=None, client=None, **extra):
        target = client or self.client
        request = RequestFactory().get("/")
        request.session = target.session
        record_authentication(request, user or self.user, method, **extra)
        if at is not None:
            request.session["account_authentication_methods"][-1]["at"] = at
        request.session.save()

    def factor(self, user=None):
        secret = generate_totp_secret()
        return TOTP.activate(user or self.user, secret).instance, secret

    def confirm(self, secret, *, client=None):
        code = format_hotp_value(hotp_value(secret, int(time.time()) // 30))
        response = (client or self.client).post(reverse("mfa_reauthenticate"), {"code": code})
        self.assertEqual(response.status_code, 302)

    def bundle(self, **changes):
        return create_request_bundle(self.user, **{
            "action": "correction", "details": "Correct my supplied attribution.",
            "reply_to": "reply@example.org", **changes,
        })

    def staff_client(self, *, can_view=True, confirm=True):
        staff = User.objects.create_user(username="privacy-operator", is_staff=True, is_superuser=can_view)
        client = Client()
        client.force_login(staff)
        _, secret = self.factor(staff)
        if confirm:
            self.confirm(secret, client=client)
        return client

    def test_downloads_require_recent_bound_first_factor_and_csrf(self):
        for name in ("account_privacy_export", "account_privacy_request"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)
            self.assertEqual(self.client.post(reverse(name)).status_code, 403)
        self.record("password", reauthenticated=True)
        self.assertEqual(self.client.post(reverse("account_privacy_export")).status_code, 403)
        self.record(at=time.time() - 301)
        self.assertEqual(self.client.post(reverse("account_privacy_export")).status_code, 403)
        self.record(at=time.time() + 60)
        self.assertEqual(self.client.post(reverse("account_privacy_export")).status_code, 403)
        self.record(user=self.other)
        self.assertEqual(self.client.post(reverse("account_privacy_export")).status_code, 403)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.record(client=csrf_client)
        self.assertEqual(csrf_client.post(reverse("account_privacy_export")).status_code, 403)
        self.assertEqual(csrf_client.post(reverse("account_privacy_request"), {"action": "closure", "details": "Close my account", "confirm": "on"}).status_code, 403)

    def test_enrolled_authenticator_requires_actual_recent_mfa_for_both_downloads(self):
        _, secret = self.factor()
        self.record()
        for name in ("account_privacy_export", "account_privacy_request"):
            self.assertEqual(self.client.post(reverse(name)).status_code, 403)
        self.confirm(secret)
        self.assertEqual(self.client.post(reverse("account_privacy_export")).status_code, 200)
        self.assertEqual(self.client.post(reverse("account_privacy_request"), {"action": "access", "details": "Review my retained historical data.", "confirm": "on"}).status_code, 200)

    def test_export_contains_only_owned_content_and_own_metadata(self):
        owned = Submission.objects.create(owner=self.user, title="Owned draft", collaborators=[{"name": "Public Author", "role": "author", "orcid": "0000-0002-1825-0097", "unexpected_secret": "nested-secret"}])
        private = Submission.objects.create(owner=self.other, title="other-private-title", abstract="other-private-abstract")
        old_revision = SubmissionRevision.objects.create(submission=private, number=1, submitted_by=self.user, reviewed_by=self.other, snapshot={"abstract": "lost-editor-history"}, review_note="private-review-note")
        self.identity.extra_data = {"private_payload": "unretained-provider-profile"}
        self.identity.save()
        other_identity = SocialAccount.objects.create(user=self.other, provider="gitlab", uid="other-private-provider-id")
        auth, _ = self.factor()
        recovery = RecoveryCodes.activate(self.user)
        attempt = VerificationAttempt.objects.create(submission=owned, user=self.user, method="github", repository_url="https://github.com/requester/research", repository_id=1, repository_owner_id=2, full_name="requester/research", token_hash="verification-credential-must-not-export", expires_at=timezone.now() + timedelta(minutes=10))
        RepositoryVerification.objects.create(attempt=attempt, submission=owned, user=self.user, method="github", repository_url=attempt.repository_url, repository_id=1, repository_owner_id=2, full_name=attempt.full_name, valid_until=timezone.now() + timedelta(days=1))
        data = account_export(self.user)
        self.assertEqual({row["id"] for row in data["projects"]}, {str(owned.pk)})
        self.assertEqual(data["own_submission_actions"], [{"action": "submitted", "project_id": str(private.pk), "revision": old_revision.number, "at": old_revision.created_at.isoformat()}])
        text = json.dumps(data)
        for secret in (private.title, private.abstract, "lost-editor-history", "private-review-note", "nested-secret", "unretained-provider-profile", other_identity.uid, self.other.email, attempt.token_hash, auth.data["secret"], recovery.instance.data["seed"], self.user.password):
            self.assertNotIn(secret, text)
        self.assertIn("Public Author", text)
        self.user.is_staff = self.user.is_superuser = True
        self.user.save()
        self.assertEqual({row["id"] for row in account_export(self.user)["projects"]}, {str(owned.pk)})

    def test_export_route_ignores_other_user_identifiers(self):
        own = Submission.objects.create(owner=self.user, title="own-draft")
        Submission.objects.create(owner=self.other, title="other-private-draft")
        self.record()
        data = self.client.post(reverse("account_privacy_export"), {"user_id": self.other.pk}).json()
        self.assertEqual(data["account"]["id"], str(self.user.pk))
        self.assertEqual([row["id"] for row in data["projects"]], [str(own.pk)])
        self.assertNotIn("other-private-draft", json.dumps(data))

    def test_current_own_orcid_provenance_exports_without_other_people_or_extra_fields(self):
        own_orcid = "https://orcid.org/0000-0002-1825-0097"
        other_orcid = "https://orcid.org/0000-0001-5109-3700"
        identity = SocialAccount.objects.create(user=self.user, provider="orcid", uid=own_orcid.rsplit("/", 1)[1])
        now = timezone.now().isoformat()
        def contributor(identifier):
            return {"name": "Example contributor", "orcid": identifier,
                    "orcid_authentication": {"source": "authenticated-account-selection", "provider": "orcid", "identifier": identifier,
                                             "authenticated_at": now, "collected_at": now, "unexpected_secret": "do-not-export"}}
        Submission.objects.create(owner=self.user, title="Owned draft", collaborators=[contributor(own_orcid), contributor(other_orcid)])
        exported = account_export(self.user)["projects"][0]["content"]["collaborators"]
        self.assertEqual(exported[0]["orcid_authentication"]["identifier"], own_orcid)
        self.assertEqual(exported[0]["orcid_authentication"]["collected_at"], now)
        self.assertNotIn("orcid_authentication", exported[1])
        self.assertNotIn("do-not-export", json.dumps(exported))
        identity.delete()
        exported = account_export(self.user)["projects"][0]["content"]["collaborators"]
        self.assertNotIn("orcid_authentication", exported[0])

    def test_request_is_downloaded_without_account_email_or_database_retention(self):
        self.record()
        models = list(apps.get_models())
        counts = {model: model.objects.count() for model in models}
        text = "Unique private correction request that is not stored."
        response = self.client.post(reverse("account_privacy_request"), {"action": "correction", "details": text, "reply_to": "voluntary-reply@example.org", "confirm": "on", "user_id": self.other.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        result = verify_request_bundle(response.content.decode())
        self.assertEqual(result["account_id"], str(self.user.pk))
        self.assertEqual(result["request"]["details"], text)
        self.assertEqual(result["request"]["reply_to"], "voluntary-reply@example.org")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "")
        self.assertFalse(EmailAddress.objects.filter(user=self.user).exists())
        self.assertEqual(counts, {model: model.objects.count() for model in models})
        self.assertNotIn(text, json.dumps(dict(self.client.session)))
        self.assertNotIn(response.json()["proof"], json.dumps(dict(self.client.session)))
        self.assertEqual(len(mail.outbox), 0)

    def test_confirmation_binds_action_exact_text_and_reply_destination(self):
        original = self.bundle()
        for field, value in (("action", "closure"), ("details", "Delete another person's work."), ("reply_to", "attacker@example.org")):
            changed = copy.deepcopy(original)
            changed["request"][field] = value
            with self.subTest(field=field), self.assertRaises(PrivacyRequestError):
                verify_request_bundle(json.dumps(changed))
        claims = signing.loads(original["proof"], salt=REQUEST_SIGNING_SALT)
        self.assertNotEqual(claims["auth_version"], self.user.get_session_auth_hash())
        self.assertNotIn(self.user.password, json.dumps(original))
        self.assertEqual(len(claims["auth_version"]), 64)

    def test_expiry_future_issuance_wrong_salt_and_malformed_files_are_rejected(self):
        now = time.time()
        with patch("time.time", return_value=now - REQUEST_MAX_AGE - 5):
            expired = self.bundle()
        with patch("time.time", return_value=now + 60):
            future = self.bundle()
        other_salt = self.bundle()
        claims = signing.loads(other_salt["proof"], salt=REQUEST_SIGNING_SALT)
        other_salt["proof"] = signing.dumps(claims, salt="different-purpose")
        for raw in (json.dumps(expired), json.dumps(future), json.dumps(other_salt), "{}", "[1]", '{"format":"x","format":"y"}', "x" * 16385):
            with self.subTest(raw_length=len(raw)), self.assertRaises(PrivacyRequestError):
                verify_request_bundle(raw)

    def test_confirmation_remains_valid_within_seven_days(self):
        with patch("time.time", return_value=time.time() - 6 * 24 * 60 * 60):
            bundle = self.bundle()
        verified = verify_request_bundle(json.dumps(bundle))
        self.assertEqual((verified["expires_at"] - verified["issued_at"]).total_seconds(), REQUEST_MAX_AGE)

    @override_settings(DEBUG=False)
    def test_request_fields_are_redacted_if_authentication_check_raises(self):
        captured = []

        def failed_check(request):
            captured.append(request)
            raise RuntimeError("Simulated authentication lookup failure")

        with patch("accounts.privacy_views.fresh_bound_first_factor", side_effect=failed_check), self.assertRaises(RuntimeError):
            self.client.post(reverse("account_privacy_request"), {"details": "private request text", "reply_to": "private-reply@example.org"})
        safe = SafeExceptionReporterFilter().get_post_parameters(captured[0])
        self.assertNotIn("private request text", str(safe))
        self.assertNotIn("private-reply@example.org", str(safe))

    def test_proof_stays_valid_after_normal_login_totp_and_recovery_usage(self):
        factor, secret = self.factor()
        recovery = RecoveryCodes.activate(self.user)
        before = self.bundle()
        self.user.last_login = timezone.now()
        self.user.save(update_fields=["last_login"])
        self.identity.last_login = timezone.now()
        self.identity.save(update_fields=["last_login"])
        factor.record_usage()
        recovery.mark_as_viewed()
        self.assertTrue(recovery.validate_code(recovery.get_unused_codes()[0]))
        self.confirm(secret)
        self.assertEqual(verify_request_bundle(json.dumps(before))["account_id"], str(self.user.pk))

    def test_security_changes_invalidate_existing_proofs(self):
        before = self.bundle()
        self.user.set_unusable_password()
        self.user.save()
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))
        before = self.bundle()
        self.user.is_active = False
        self.user.save()
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))
        self.user.is_active = True
        self.user.save()
        before = self.bundle()
        linked = SocialAccount.objects.create(user=self.user, provider="gitlab", uid="new-identity")
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))
        before = self.bundle()
        linked.delete()
        SocialAccount.objects.create(user=self.user, provider="gitlab", uid="new-identity")
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))
        before = self.bundle()
        email = EmailAddress.objects.create(user=self.user, email="verified@example.org", verified=False)
        self.assertEqual(verify_request_bundle(json.dumps(before))["account_id"], str(self.user.pk))
        email.verified = True
        email.save()
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))
        before = self.bundle()
        email.delete()
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))

    def test_factor_security_changes_and_recovery_regeneration_invalidate_proofs(self):
        before = self.bundle()
        factor, _ = self.factor()
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))
        recovery = RecoveryCodes.activate(self.user)
        before = self.bundle()
        factor.data["secret"] = "changed-encrypted-totp-material"
        factor.save()
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))
        before = self.bundle()
        recovery.instance.delete()
        RecoveryCodes.activate(self.user)
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))
        before = self.bundle()
        factor.delete()
        with self.assertRaises(PrivacyRequestError):
            verify_request_bundle(json.dumps(before))

    def test_legacy_recovery_code_consumption_does_not_change_auth_version(self):
        from .mfa_adapter import MFAAdapter
        factor = Authenticator.objects.create(user=self.user, type="recovery_codes", data={"migrated_codes": [MFAAdapter().encrypt("11111111"), MFAAdapter().encrypt("22222222")], "used_mask": 0})
        before = authentication_version(self.user)
        self.assertTrue(factor.wrap().validate_code("11111111"))
        self.assertEqual(authentication_version(self.user), before)

    def test_staff_verifier_requires_mfa_and_account_view_permission(self):
        bundle = json.dumps(self.bundle())
        self.assertEqual(self.client.post(reverse("privacy_request_verify"), {"bundle": bundle}).status_code, 403)
        staff = self.staff_client(confirm=False)
        self.assertEqual(staff.post(reverse("privacy_request_verify"), {"bundle": bundle}).status_code, 403)
        User.objects.get(username="privacy-operator").delete()
        limited_staff = self.staff_client(can_view=False)
        self.assertEqual(limited_staff.post(reverse("privacy_request_verify"), {"bundle": bundle}).status_code, 403)

    def test_staff_verifier_escapes_content_and_performs_no_mutation_even_on_replay(self):
        bundle = self.bundle(action="closure", details="<script>alert('not executed')</script>", reply_to="")
        staff = self.staff_client()
        version = authentication_version(self.user)
        for _ in range(2):
            response = staff.post(reverse("privacy_request_verify"), {"bundle": json.dumps(bundle)})
            self.assertContains(response, "Verified request — no action performed")
            self.assertContains(response, "&lt;script&gt;")
            self.assertNotContains(response, "<script>alert")
            self.assertContains(response, "No reply address was authorized")
            self.assertNotContains(response, bundle["proof"])
            self.assertIn("no-store", response["Cache-Control"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(authentication_version(self.user), version)
        self.assertFalse(LogEntry.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(ACCOUNT_RATE_LIMITS={"privacy_export": "1/h/user", "privacy_confirmation": "1/h/user"})
    def test_sensitive_downloads_are_rate_limited(self):
        self.record()
        for name, data in (("account_privacy_export", {}), ("account_privacy_request", {"action": "access", "details": "Review my data", "confirm": "on"})):
            self.assertEqual(self.client.post(reverse(name), data).status_code, 200)
            limited = self.client.post(reverse(name), data)
            self.assertEqual(limited.status_code, 429)
            self.assertIn("no-store", limited["Cache-Control"])

    def test_request_form_requires_confirmation_and_bounds_input(self):
        self.record()
        for data in (
            {"action": "access", "details": "No confirmation checkbox"},
            {"action": "access", "details": "x" * 2001, "confirm": "on"},
            {"action": "unknown", "details": "Invalid action", "confirm": "on"},
            {"action": "access", "details": "Invalid reply address", "reply_to": "not-an-address", "confirm": "on"},
        ):
            response = self.client.post(reverse("account_privacy_request"), data)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("Content-Disposition", response)
