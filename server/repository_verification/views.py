"""Owner-only repository authorization screens; OAuth credentials stay transient."""

from allauth.core import ratelimit
from allauth.socialaccount.adapter import get_adapter
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.debug import sensitive_variables

from submissions.models import Submission

from .github import GitHubError
from .models import VerificationAttempt
from .services import (
    challenge_document,
    check_file,
    has_current_verification,
    start_attempt,
)


def _owned_submission(request, pk):
    # Listing a coauthor or granting editing access does not grant this claim.
    return get_object_or_404(Submission, pk=pk, owner=request.user)


def _session_key(request, submission):
    return f"mathstoa_verification_file:{request.user.pk}:{submission.pk}"


def _rate_limit_response(request, submission, *, destination="verification:overview"):
    if not ratelimit.consume(
        request, action="repository_verification", user=request.user,
        key=f"user:{request.user.pk}:submission:{submission.pk}",
    ):
        messages.error(request, "Too many verification attempts. Please wait and try again later; the limit resets within an hour.")
        return redirect(destination, pk=submission.pk)
    return None


def _github_provider(request):
    try:
        provider = get_adapter(request).get_provider(request, "github")
        if provider.app.client_id and provider.app.secret:
            return provider
    except (ObjectDoesNotExist, ImproperlyConfigured):
        pass
    return None


def _error(request, error):
    if isinstance(error, ValidationError):
        message = " ".join(error.messages)
    else:
        message = str(error)
    messages.error(request, message)


def _verification_context(submission):
    return {
        "submission": submission,
        # A cached proof can still exist after its live recheck failed. The
        # received version remains blocked until verification succeeds again.
        "verified": has_current_verification(submission) and not submission.revisions.filter(
            number=submission.revision, status="unverified",
        ).exists(),
    }


@login_required
@require_GET
@never_cache
def overview(request, pk):
    submission = _owned_submission(request, pk)
    return render(request, "repository_verification/overview.html", {
        **_verification_context(submission),
        "github_available": _github_provider(request) is not None,
    })


@login_required
@require_GET
@never_cache
def file_overview(request, pk):
    submission = _owned_submission(request, pk)
    key = _session_key(request, submission)
    file_state = request.session.get(key)
    attempt = None
    if isinstance(file_state, dict):
        try:
            attempt = VerificationAttempt.objects.filter(
                pk=file_state.get("attempt_id"), submission=submission,
                user=request.user, method="file", consumed_at__isnull=True,
                revoked_at__isnull=True, expires_at__gt=timezone.now(),
                repository_url=submission.repository_url,
            ).first()
        except (ValueError, ValidationError):
            pass
        if not attempt:
            request.session.pop(key, None)
            file_state = None
    return render(request, "repository_verification/file.html", {
        **_verification_context(submission),
        "file_attempt": attempt,
        "file_content": file_state.get("content", "") if attempt else "",
    })


@login_required
@require_GET
@never_cache
def github_overview(request, pk):
    submission = _owned_submission(request, pk)
    return render(request, "repository_verification/github.html", {
        **_verification_context(submission),
        "github_available": _github_provider(request) is not None,
    })


@login_required
@require_POST
@never_cache
@sensitive_variables("token")
def issue_file(request, pk):
    submission = _owned_submission(request, pk)
    limited = _rate_limit_response(request, submission, destination="verification:file_overview")
    if limited:
        return limited
    try:
        attempt, token = start_attempt(submission, request.user, "file")
        request.session[_session_key(request, submission)] = {
            "attempt_id": str(attempt.pk),
            "content": challenge_document(attempt, token),
        }
        messages.info(request, "Add the verification file below to your repository's default branch, then check it here.")
    except (ValidationError, GitHubError) as error:
        _error(request, error)
    return redirect("verification:file_overview", pk=submission.pk)


@login_required
@require_POST
@never_cache
def verify_file(request, pk):
    submission = _owned_submission(request, pk)
    limited = _rate_limit_response(request, submission, destination="verification:file_overview")
    if limited:
        return limited
    key = _session_key(request, submission)
    file_state = request.session.get(key, {})
    attempt_id = file_state.get("attempt_id") if isinstance(file_state, dict) else None
    if not attempt_id:
        messages.error(request, "Generate a verification file before checking the repository.")
        return redirect("verification:file_overview", pk=submission.pk)
    try:
        attempt = get_object_or_404(
            VerificationAttempt, pk=attempt_id, submission=submission,
            user=request.user, method="file",
        )
        check_file(attempt, request.user)
        request.session.pop(key, None)
        messages.success(request, "Repository authorization verified. Any submitted revision awaiting this check is now ready for moderation.")
        return redirect("submissions:detail", pk=submission.pk)
    except (ValidationError, GitHubError) as error:
        _error(request, error)
    return redirect("verification:file_overview", pk=submission.pk)


@login_required
@require_POST
@never_cache
@sensitive_variables("nonce")
def github_start(request, pk):
    submission = _owned_submission(request, pk)
    limited = _rate_limit_response(request, submission, destination="verification:github_overview")
    if limited:
        return limited
    provider = _github_provider(request)
    if provider is None:
        messages.error(request, "GitHub verification is not configured yet. You can verify by file instead.")
        return redirect("verification:github_overview", pk=submission.pk)
    try:
        attempt, nonce = start_attempt(submission, request.user, "github")
        request.session.pop(_session_key(request, submission), None)
        return provider.redirect(
            request, process="connect",
            next_url=reverse("verification:github_overview", kwargs={"pk": submission.pk}),
            data={"mathstoa_verification": str(attempt.pk), "nonce": nonce},
            scope=[],
        )
    except (ValidationError, GitHubError) as error:
        _error(request, error)
    return redirect("verification:github_overview", pk=submission.pk)
