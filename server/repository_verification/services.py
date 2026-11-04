"""Short-lived challenges and audited, time-bounded repository authorization.

All host requests are performed by GitHubClient, never by fetching submitted URLs.
Tokens are kept only as digests. OAuth credentials never enter the database.
"""
import hashlib
import json
import secrets
from datetime import timedelta

from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from submissions.models import Submission, SubmissionRevision
from .github import GitHubClient, GitHubError
from .models import RepositoryVerification, VerificationAttempt


AUTHORIZATION = "I authorize this mathStoa account to submit this repository."
# Files generated with earlier wordings stay valid until their attempt expires.
ACCEPTED_AUTHORIZATIONS = (
    AUTHORIZATION,
    "I authorize this mathStoa account to submit this repository for review.",
    "I authorize this Math Stoa account to submit this repository for review.",
)


class VerificationError(ValidationError):
    pass


def authenticated_orcid(user):
    # Only identifiers obtained by the login provider; never a typed author field.
    return (SocialAccount.objects.filter(user=user, provider="orcid")
            .order_by("pk").values_list("uid", flat=True).first() or "")


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _owner(submission, user):
    if not user.is_authenticated or not user.is_active or submission.owner_id != user.pk:
        raise VerificationError("Only the submission owner can verify repository authorization.")


def _api(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except GitHubError as exc:
        raise VerificationError(str(exc)) from None


def _client():
    return GitHubClient(public_token=getattr(settings, "REPOSITORY_GITHUB_READ_TOKEN", None))


def _same_repository(record, repository):
    if (record.repository_id != repository.repository_id
            or record.repository_owner_id != repository.owner_id
            or record.full_name.casefold() != repository.full_name.casefold()):
        raise VerificationError("The repository has moved or changed owner. Start a new verification.")


def _assert_attempt(attempt, user, method):
    submission = attempt.submission
    _owner(submission, user)
    if attempt.user_id != user.pk or attempt.method != method:
        raise VerificationError("This verification belongs to a different account or method.")
    if attempt.consumed_at or attempt.revoked_at or attempt.expires_at <= timezone.now():
        raise VerificationError("This verification has expired or was already used. Start a new verification.")
    if attempt.repository_url != submission.repository_url or attempt.orcid != authenticated_orcid(user):
        raise VerificationError("The repository or connected ORCID has changed. Start a new verification.")


def start_attempt(submission, user, method, client=None):
    if method not in {"file", "github"}:
        raise VerificationError("Choose a supported verification method.")
    _owner(submission, user)
    original_url = submission.repository_url
    client = client or _client()
    repository = _api(client.resolve_repository, original_url)
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    with transaction.atomic():
        locked = Submission.objects.select_for_update().get(pk=submission.pk)
        _owner(locked, user)
        if locked.repository_url != original_url:
            raise VerificationError("The repository changed. Reload the submission and try again.")
        # Invalidate both kinds, so reissuing can never leave a stale callback live.
        VerificationAttempt.objects.filter(submission=locked, consumed_at__isnull=True,
                                           revoked_at__isnull=True).update(revoked_at=now)
        attempt = VerificationAttempt.objects.create(
            submission=locked, user=user, method=method, repository_url=original_url,
            repository_id=repository.repository_id, repository_owner_id=repository.owner_id,
            full_name=repository.full_name, orcid=authenticated_orcid(user), token_hash=_digest(token),
            expires_at=now + (timedelta(hours=24) if method == "file" else timedelta(minutes=10)),
        )
    return attempt, token


def _document_fields(attempt):
    return {
        "version": 1, "submission": str(attempt.submission_id),
        "repository": f"https://github.com/{attempt.full_name}",
        "repository_id": attempt.repository_id, "repository_owner_id": attempt.repository_owner_id,
        "orcid": attempt.orcid, "authorization": AUTHORIZATION,
    }


def challenge_document(attempt, token):
    if not isinstance(token, str) or not secrets.compare_digest(_digest(token), attempt.token_hash):
        raise VerificationError("Invalid verification code. Generate a new file.")
    return json.dumps({**_document_fields(attempt), "token": token}, indent=2) + "\n"


def _complete(attempt, user, repository, **evidence):
    # Lock in the same order as submission/review transitions. Conditional consume
    # also protects against replay on SQLite, where row locking is not supported.
    with transaction.atomic():
        submission = Submission.objects.select_for_update().get(pk=attempt.submission_id)
        locked = VerificationAttempt.objects.select_for_update().get(pk=attempt.pk)
        locked.submission = submission
        _assert_attempt(locked, user, attempt.method)
        _same_repository(locked, repository)
        now = timezone.now()
        changed = VerificationAttempt.objects.filter(
            pk=locked.pk, consumed_at__isnull=True, revoked_at__isnull=True, expires_at__gt=now,
        ).update(consumed_at=now)
        if changed != 1:
            raise VerificationError("This verification was already used. Start a new verification.")
        proof = RepositoryVerification.objects.create(
            attempt=locked, submission=submission, user=user, method=locked.method,
            repository_url=locked.repository_url, repository_id=locked.repository_id,
            repository_owner_id=locked.repository_owner_id, full_name=locked.full_name, orcid=locked.orcid,
            valid_until=now + timedelta(days=getattr(settings, "REPOSITORY_VERIFICATION_VALID_DAYS", 30)),
            **evidence,
        )
        # The owner already submitted and accepted the frozen version. Both
        # verification methods complete that same request without another POST
        # or a second contributor email. Older versions stay superseded.
        SubmissionRevision.objects.filter(
            submission=submission, number=submission.revision, status="unverified",
            snapshot__repository_url=proof.repository_url,
        ).update(status="pending")
        return proof


def check_file(attempt, user, client=None):
    attempt = VerificationAttempt.objects.select_related("submission").get(pk=attempt.pk)
    _assert_attempt(attempt, user, "file")
    client = client or _client()
    repository = _api(client.resolve_repository, attempt.repository_url)
    _same_repository(attempt, repository)
    evidence = _api(client.read_verification_file, repository)
    _same_repository(attempt, evidence.repository)
    try:
        document = json.loads(evidence.content)
    except (ValueError, TypeError):
        raise VerificationError("The verification file is not valid JSON. Copy the complete file shown here.") from None
    if (not isinstance(document, dict) or document.get("authorization") not in ACCEPTED_AUTHORIZATIONS
            or any(document.get(k) != v for k, v in _document_fields(attempt).items() if k != "authorization")):
        raise VerificationError("The file does not authorize this submission and account. Copy the current verification file.")
    token = document.get("token")
    if not isinstance(token, str) or len(token) > 200 or not secrets.compare_digest(_digest(token), attempt.token_hash):
        raise VerificationError("The file contains a different verification code. Copy the current verification file.")
    # A transfer during fetching must not authorize the new owner by accident.
    current = _api(client.resolve_repository, attempt.repository_url)
    _same_repository(attempt, current)
    return _complete(attempt, user, current, commit_sha=evidence.commit_sha, role="endorsement")


@sensitive_variables("access_token", "raw_nonce")
def check_github(attempt, user, raw_nonce, access_token, expected_github_uid=None, client=None):
    attempt = VerificationAttempt.objects.select_related("submission").get(pk=attempt.pk)
    _assert_attempt(attempt, user, "github")
    if (not isinstance(raw_nonce, str) or len(raw_nonce) > 200
            or not secrets.compare_digest(_digest(raw_nonce), attempt.token_hash)):
        raise VerificationError("The GitHub verification session is invalid. Try connecting again.")
    if not isinstance(access_token, str) or not access_token:
        raise VerificationError("GitHub did not provide an authorization credential. Try connecting again.")
    client = client or _client()
    repository = _api(client.resolve_repository, attempt.repository_url)
    _same_repository(attempt, repository)
    evidence = _api(client.verify_user_access, repository, access_token)
    _same_repository(attempt, evidence.repository)
    if expected_github_uid is None or str(evidence.github_user_id) != str(expected_github_uid):
        raise VerificationError("The authenticated GitHub identity did not match. Try connecting again.")
    if SocialAccount.objects.filter(provider="github", uid=str(evidence.github_user_id)).exclude(user=user).exists():
        raise VerificationError("This GitHub account is connected to another mathΣτοά account.")
    current = _api(client.resolve_repository, attempt.repository_url)
    _same_repository(attempt, current)
    return _complete(attempt, user, current, github_user_id=evidence.github_user_id,
                     github_login=evidence.github_login, role=evidence.role)


def _current_proof(submission, *, lock=False):
    proofs = RepositoryVerification.objects.filter(
        submission=submission, user_id=submission.owner_id, repository_url=submission.repository_url,
        orcid=authenticated_orcid(submission.owner), revoked_at__isnull=True,
        valid_until__gt=timezone.now(),
    )
    if lock:
        proofs = proofs.select_for_update()
    return proofs.first()


def has_current_verification(submission):
    """Local hint only; reusing a proof for moderation/publication requires a live check."""
    return _current_proof(submission) is not None


def require_verified(submission, actor=None, client=None):
    """Gate submission and approval; fail closed on unavailable or changed repos.

    Authorization is attested at verified_at, valid for a bounded period. Public
    rechecks detect transfers/replacements/privacy changes, not revoked user roles.
    """
    current_submission = Submission.objects.select_related("owner").get(pk=submission.pk)
    if current_submission.repository_url != submission.repository_url or current_submission.owner_id != submission.owner_id:
        raise VerificationError("The submission changed. Reload and verify its repository again.")
    # Submit/review callers hold a surrounding transaction and submission lock.
    # Lock the proof too so concurrent identity revocation cannot slip between
    # this gate and the transition's commit. Standalone reads are display only.
    proof = _current_proof(current_submission, lock=connection.in_atomic_block)
    if proof is None:
        raise VerificationError("The submission owner must verify repository authorization before this submission can proceed.")
    client = client or _client()
    repository = _api(client.resolve_repository, proof.repository_url)
    try:
        _same_repository(proof, repository)
    except VerificationError:
        RepositoryVerification.objects.filter(pk=proof.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
        raise
    # Guard callers that do not already hold a submission lock during the fetch.
    current_submission.refresh_from_db()
    if (current_submission.repository_url != proof.repository_url or current_submission.owner_id != proof.user_id
            or authenticated_orcid(current_submission.owner) != proof.orcid):
        raise VerificationError("The submission changed during verification. Please try again.")
    proof.refresh_from_db()
    if proof.revoked_at or proof.valid_until <= timezone.now():
        raise VerificationError("Repository verification has expired. Verify again to proceed.")
    return proof
