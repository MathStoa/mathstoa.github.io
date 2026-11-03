"""Explicit self-export and stateless, human-reviewed privacy confirmations."""
import hashlib
import json
import time
from datetime import datetime, timezone as datetime_timezone

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.debug import sensitive_variables

from repository_verification.models import RepositoryVerification, VerificationAttempt
from submissions.models import EditorInvitation, Submission, SubmissionRevision

REQUEST_ACTIONS = (("access", "Access or additional information"), ("correction", "Correct information"), ("closure", "Close my account or remove information"))
REQUEST_MAX_AGE = 7 * 24 * 60 * 60
REQUEST_TEXT_MAX_LENGTH = 2000
REQUEST_BUNDLE_MAX_BYTES = 16384
REQUEST_SIGNING_SALT = "mathstoa.privacy-request.confirmation.v1"
AUTH_VERSION_SALT = "mathstoa.privacy-request.auth-version.v1"
INVALID_REQUEST = "This request file is invalid, expired, or no longer matches the account's security settings. Ask the requester to create a new file."


class PrivacyRequestError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _date(value):
    return value.isoformat() if value else None


@sensitive_variables()
def authentication_version(user):
    """Hash security state, never activity counters or decryptable credentials."""
    factors = []
    for factor in Authenticator.objects.filter(user=user).order_by("pk"):
        if factor.type == Authenticator.Type.TOTP:
            security = {"secret": factor.data.get("secret")}
        elif factor.type == Authenticator.Type.RECOVERY_CODES:
            # Seed and row identity are stable while codes are used/viewed.
            # Legacy migrated lists shrink on use, so never fingerprint that list.
            security = {"seed": factor.data.get("seed"), "legacy": "migrated_codes" in factor.data}
        elif factor.type == Authenticator.Type.WEBAUTHN:
            security = {"credential": factor.data.get("credential")}
        else:
            raise PrivacyRequestError(INVALID_REQUEST)
        factors.append([factor.pk, factor.type, security])
    state = {
        "user": str(user.pk), "active": user.is_active, "password": user.password,
        "identities": list(SocialAccount.objects.filter(user=user).order_by("pk").values_list("pk", "provider", "uid")),
        "verified_emails": list(EmailAddress.objects.filter(user=user, verified=True).order_by("pk").values_list("pk", "email", "primary")),
        "factors": factors,
    }
    # Separate HMAC purpose; neither the password nor a session auth hash is returned.
    return salted_hmac(AUTH_VERSION_SALT, _canonical(state), algorithm="sha256").hexdigest()


def _validate_request_body(body):
    if not isinstance(body, dict) or set(body) != {"action", "details", "reply_to"}:
        raise PrivacyRequestError(INVALID_REQUEST)
    if not isinstance(body["action"], str) or body["action"] not in dict(REQUEST_ACTIONS):
        raise PrivacyRequestError(INVALID_REQUEST)
    if not isinstance(body["details"], str) or not 1 <= len(body["details"]) <= REQUEST_TEXT_MAX_LENGTH or not body["details"].strip():
        raise PrivacyRequestError(INVALID_REQUEST)
    if not isinstance(body["reply_to"], str) or len(body["reply_to"]) > 254:
        raise PrivacyRequestError(INVALID_REQUEST)
    if body["reply_to"]:
        try:
            validate_email(body["reply_to"])
        except ValidationError as exc:
            raise PrivacyRequestError(INVALID_REQUEST) from exc
    try:
        _canonical(body)
    except (ValueError, UnicodeError) as exc:
        raise PrivacyRequestError(INVALID_REQUEST) from exc


@sensitive_variables()
def create_request_bundle(user, *, action, details, reply_to=""):
    if not user.is_active:
        raise PrivacyRequestError(INVALID_REQUEST)
    body = {"action": action, "details": details, "reply_to": reply_to}
    _validate_request_body(body)
    claims = {
        "version": 1, "subject": str(user.pk), "issued_at": int(time.time()),
        "request_digest": hashlib.sha256(_canonical(body)).hexdigest(),
        "auth_version": authentication_version(user),
    }
    return {
        "format": "mathstoa-privacy-request", "version": 1, "request": body,
        "proof": signing.dumps(claims, salt=REQUEST_SIGNING_SALT, compress=False),
    }


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PrivacyRequestError(INVALID_REQUEST)
        result[key] = value
    return result


@sensitive_variables()
def verify_request_bundle(raw):
    """Verify only: never execute the request or change any account/record."""
    try:
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > REQUEST_BUNDLE_MAX_BYTES:
            raise PrivacyRequestError(INVALID_REQUEST)
        bundle = json.loads(raw, object_pairs_hook=_unique_keys)
        if not isinstance(bundle, dict) or set(bundle) != {"format", "version", "request", "proof"}:
            raise PrivacyRequestError(INVALID_REQUEST)
        if bundle["format"] != "mathstoa-privacy-request" or type(bundle["version"]) is not int or bundle["version"] != 1:
            raise PrivacyRequestError(INVALID_REQUEST)
        _validate_request_body(bundle["request"])
        if not isinstance(bundle["proof"], str) or len(bundle["proof"]) > 4096:
            raise PrivacyRequestError(INVALID_REQUEST)
        claims = signing.loads(bundle["proof"], salt=REQUEST_SIGNING_SALT, max_age=REQUEST_MAX_AGE)
        if not isinstance(claims, dict) or set(claims) != {"version", "subject", "issued_at", "request_digest", "auth_version"}:
            raise PrivacyRequestError(INVALID_REQUEST)
        if type(claims["version"]) is not int or claims["version"] != 1 or not isinstance(claims["subject"], str) or len(claims["subject"]) > 128:
            raise PrivacyRequestError(INVALID_REQUEST)
        issued_at = claims["issued_at"]
        if type(issued_at) is not int or not 0 <= time.time() - issued_at <= REQUEST_MAX_AGE:
            raise PrivacyRequestError(INVALID_REQUEST)
        if not isinstance(claims["request_digest"], str) or not isinstance(claims["auth_version"], str):
            raise PrivacyRequestError(INVALID_REQUEST)
        digest = hashlib.sha256(_canonical(bundle["request"])).hexdigest()
        if not constant_time_compare(claims["request_digest"], digest):
            raise PrivacyRequestError(INVALID_REQUEST)
        user = get_user_model().objects.get(pk=claims["subject"], is_active=True)
        if not constant_time_compare(claims["auth_version"], authentication_version(user)):
            raise PrivacyRequestError(INVALID_REQUEST)
        return {
            "account_id": str(user.pk), "username": user.username,
            "identities": list(SocialAccount.objects.filter(user=user).order_by("pk").values("provider", "uid")),
            "action_label": dict(REQUEST_ACTIONS)[bundle["request"]["action"]],
            "request": bundle["request"],
            "issued_at": datetime.fromtimestamp(issued_at, datetime_timezone.utc),
            "expires_at": datetime.fromtimestamp(issued_at + REQUEST_MAX_AGE, datetime_timezone.utc),
        }
    except (signing.BadSignature, ValueError, TypeError, UnicodeError, RecursionError, get_user_model().DoesNotExist) as exc:
        raise PrivacyRequestError(INVALID_REQUEST) from exc


@sensitive_variables()
def account_export(user):
    """Current account data only; broader historical access needs manual review."""
    projects = []
    accessible = Submission.objects.filter(Q(owner=user) | Q(editors=user)).distinct().order_by("pk")
    for project in accessible:
        content = {field: getattr(project, field) for field in ("title", "repository_url", "doi", "reading_url", "abstract", "category", "secondary_categories")}
        # Drafts saved before contributors became name and ORCID only can still hold roles and affiliations.
        content["collaborators"] = [
            {key: person[key] for key in ("name", "role", "orcid", "affiliation") if isinstance(person.get(key), str)}
            for person in project.collaborators if isinstance(person, dict)
        ]
        projects.append({
            "id": str(project.pk), "access": "owner" if project.owner_id == user.pk else "editor",
            "revision": project.revision, "created_at": _date(project.created_at),
            "updated_at": _date(project.updated_at), "content": content,
        })
    actions = []
    for revision in SubmissionRevision.objects.filter(Q(submitted_by=user) | Q(reviewed_by=user)).order_by("pk"):
        if revision.submitted_by_id == user.pk:
            actions.append({"action": "submitted", "project_id": str(revision.submission_id), "revision": revision.number, "at": _date(revision.created_at)})
        if revision.reviewed_by_id == user.pk:
            actions.append({"action": "moderated", "project_id": str(revision.submission_id), "revision": revision.number, "at": _date(revision.reviewed_at)})
    proofs = [{
        "project_id": str(row.submission_id), "method": row.method,
        "repository_url": row.repository_url, "repository_id": row.repository_id,
        "repository_owner_id": row.repository_owner_id, "repository_name": row.full_name,
        "orcid": row.orcid, "github_user_id": row.github_user_id, "github_login": row.github_login,
        "role": row.role, "commit_sha": row.commit_sha, "verified_at": _date(row.verified_at),
        "valid_until": _date(row.valid_until), "revoked_at": _date(row.revoked_at),
    } for row in RepositoryVerification.objects.filter(user=user).order_by("pk")]
    attempts = [{
        "project_id": str(row.submission_id), "method": row.method, "repository_url": row.repository_url,
        "created_at": _date(row.created_at), "expires_at": _date(row.expires_at),
        "consumed_at": _date(row.consumed_at), "revoked_at": _date(row.revoked_at),
    } for row in VerificationAttempt.objects.filter(user=user).order_by("pk")]
    return {
        "format": "mathstoa-account-data", "version": 1, "generated_at": _date(timezone.now()),
        "scope": "Account data and project drafts you can currently access. Historical snapshots, other people's information, and broader access requests are handled by the operator on request.",
        "account": {"id": str(user.pk), "username": user.username, "email": user.email, "created_at": _date(user.date_joined), "last_sign_in": _date(user.last_login)},
        "sign_in_methods": [{"provider": row.provider, "account_id": row.uid, "connected_at": _date(row.date_joined), "last_sign_in": _date(row.last_login)} for row in SocialAccount.objects.filter(user=user).order_by("pk")],
        "email_addresses": list(EmailAddress.objects.filter(user=user).order_by("pk").values("email", "verified", "primary")),
        "authenticators": [{"type": row.type, "created_at": _date(row.created_at), "last_used_at": _date(row.last_used_at)} for row in Authenticator.objects.filter(user=user).order_by("pk")],
        "projects": projects, "own_submission_actions": actions, "repository_verifications": proofs,
        "verification_attempts": attempts,
        "accepted_invitations": [{"project_id": str(row.submission_id), "accepted_at": _date(row.accepted_at)} for row in EditorInvitation.objects.filter(accepted_by=user).order_by("pk")],
    }
