import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts.middleware import limit_action

from .forms import SubmissionForm, review_form
from .erasure import erase_submission, has_static_listing
from .models import Submission, SubmissionRevision
from .notifications import notify_contributors, notify_submission_received
from .publication import accepted_declaration


erasure_logger = logging.getLogger("submissions.erasure")


def accessible(user):
    return Submission.objects.filter(owner=user)


@login_required
def submission_list(request):
    return render(request, "submissions/list.html", {"submissions": accessible(request.user).select_related("published_revision")})


def _record_submission(submission, user):
    """Freeze a complete, explicitly accepted form in the surrounding save transaction."""
    from repository_verification.services import has_current_verification, require_verified

    status = "unverified"
    if has_current_verification(submission):
        try:
            require_verified(submission, actor=user)
        except ValidationError:
            # Keep the submitted details, but do not admit them to moderation
            # when proof has expired, the repository changed or its host is down.
            pass
        else:
            status = "pending"
    submission.revisions.filter(status="unverified").update(status="superseded")
    snapshot = submission.snapshot()
    snapshot["publication_declaration"] = accepted_declaration()
    revision = SubmissionRevision.objects.create(
        submission=submission, number=submission.revision, snapshot=snapshot,
        submitted_by=user, status=status,
    )
    notify_submission_received(revision)
    return revision


def _submitted_response(request, revision):
    if revision.status == "unverified":
        messages.success(request, "Submission received. Verify by file to send this revision for moderation.")
        return redirect("verification:overview", pk=revision.submission_id)
    return redirect("submissions:detail", pk=revision.submission_id)


@login_required
def create(request):
    if request.method == "POST":
        limited = limit_action(request, action="submission_create", user=request.user, retry_after=300)
        if limited:
            return limited
    form = SubmissionForm(request.POST or None, user=request.user, for_submission=True)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            submission = form.save(commit=False)
            submission.owner = request.user
            submission.save()
            revision = _record_submission(submission, request.user)
        return _submitted_response(request, revision)
    return render(request, "submissions/form.html", {"form": form})


@login_required
def detail(request, pk):
    submission = get_object_or_404(accessible(request.user).select_related("published_revision"), pk=pk)
    context = {
        "submission": submission, "revisions": submission.revisions.all(),
        # The detail page reports the state; the form itself submits each version.
        "current_revision": submission.revisions.filter(number=submission.revision).first(),
    }
    return render(request, "submissions/detail.html", context)


@login_required
def edit(request, pk):
    with transaction.atomic():
        submission = get_object_or_404(accessible(request.user).select_for_update(), pk=pk)
        if request.method == "POST":
            limited = limit_action(request, action="submission_edit", user=request.user, key=str(submission.pk), retry_after=300)
            if limited:
                return limited
        form = SubmissionForm(request.POST or None, instance=submission, user=request.user, for_submission=True)
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["revision"] != submission.revision:
                form.add_error(None, "A newer revision has been submitted. Reload this page before editing so those changes are not overwritten.")
                return render(request, "submissions/form.html", {"form": form, "submission": submission}, status=409)
            submission = form.save(commit=False)
            submission.revision += 1
            submission.save()
            revision = _record_submission(submission, request.user)
            return _submitted_response(request, revision)
    return render(request, "submissions/form.html", {"form": form, "submission": submission})


@login_required
@require_http_methods(["GET", "POST"])
def delete(request, pk):
    with transaction.atomic():
        submission = get_object_or_404(accessible(request.user).select_for_update(), pk=pk)
        static_listing = has_static_listing(submission)
        context = {"submission": submission, "static_listing": static_listing}
        if request.method == "GET":
            return render(request, "submissions/delete.html", context)
        if static_listing:
            return render(request, "submissions/delete.html", context, status=409)
        if (
            request.POST.get("revision") != str(submission.revision)
            or request.POST.get("published_revision") != str(submission.published_revision_id or "")
        ):
            context["error"] = "The submission changed or the confirmation was incomplete. Check the details below before confirming deletion."
            return render(request, "submissions/delete.html", context, status=409)
        erase_submission(submission)
        # Keep only the identifier in operational logs for backup restoration.
        transaction.on_commit(lambda: erasure_logger.warning(
            "Submission %s deleted by its owner; re-apply this deletion after restoring a backup.", pk,
        ))
    request.session.pop(f"mathstoa_verification_file:{request.user.pk}:{pk}", None)
    messages.success(request, "Submission deleted.")
    return redirect("submissions:list")


@login_required
@require_POST
def submit(request, pk):
    with transaction.atomic():
        submission = get_object_or_404(accessible(request.user).select_for_update(), pk=pk)
        if request.POST.get("revision") != str(submission.revision):
            messages.error(request, "The submission changed. Please check the current details and submit again.")
            return redirect("submissions:detail", pk=pk)
        existing = submission.revisions.filter(number=submission.revision).first()
        if existing:
            if existing.status == "rejected":
                messages.info(request, "Please edit the submission and submit your changes.")
            else:
                messages.info(request, "This revision has already been submitted.")
            return redirect("submissions:detail", pk=pk)
        limited = limit_action(request, action="submission_submit", user=request.user, key=str(submission.pk))
        if limited:
            return limited
        form = review_form(submission, user=request.user)
        if not form.is_valid():
            messages.error(request, "Please complete the highlighted details before submitting.")
            return render(request, "submissions/form.html", {"form": form, "submission": submission}, status=400)
        if request.POST.get("publication_confirmed") != "on":
            messages.error(request, "Please read and accept the abstract licence and permission declaration before submitting this version.")
            return redirect("submissions:detail", pk=pk)
        from repository_verification.services import require_verified
        try:
            require_verified(submission, actor=request.user)
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect("submissions:detail", pk=pk)
        snapshot = submission.snapshot()
        # Record only the declaration just accepted in this request. Legacy
        # snapshots and saved permission booleans do not prove this acceptance.
        snapshot["publication_declaration"] = accepted_declaration()
        revision, created = SubmissionRevision.objects.get_or_create(submission=submission, number=submission.revision, defaults={"snapshot": snapshot, "submitted_by": request.user})
        if created:
            # The durable outbox commits with the revision; the worker sends later.
            notify_contributors(revision)
            # The submission page states the outcome; no message repeats it.
        elif revision.status == "rejected":
            messages.info(request, "Please edit the submission and submit your changes.")
        else:
            messages.info(request, "This revision has already been submitted.")
    return redirect("submissions:detail", pk=pk)


def public_archive():
    path = Path(getattr(settings, "ARCHIVE_PATH", Path(__file__).resolve().parents[2] / "docs/data/projects.json"))
    archive = json.loads(path.read_text(encoding="utf-8"))
    for project in archive["projects"]:
        for credit in project["credits"]:
            if credit.get("orcid"):
                credit.setdefault("orcidAuthenticated", False)
    for submission in Submission.objects.exclude(published_revision=None).select_related("published_revision"):
        archive["projects"].append(submission.published_revision.public_project())
    return archive


def archive_json(request):
    response = JsonResponse(public_archive())
    response["Cache-Control"] = "public, max-age=60"
    return response
