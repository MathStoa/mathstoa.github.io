import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.middleware import limit_action

from .forms import SubmissionForm, review_form
from .models import Submission, SubmissionRevision
from .notifications import notify_contributors
from .publication import ABSTRACT_LICENSE, DECLARATION, accepted_declaration


def accessible(user):
    return Submission.objects.filter(owner=user)


@login_required
def submission_list(request):
    return render(request, "submissions/list.html", {"submissions": accessible(request.user).select_related("published_revision")})


@login_required
def create(request):
    if request.method == "POST":
        limited = limit_action(request, action="submission_create", user=request.user, retry_after=300)
        if limited:
            return limited
    form = SubmissionForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        submission = form.save(commit=False)
        submission.owner = request.user
        submission.save()
        messages.success(request, "Draft saved. Complete the details, then submit it for moderation.")
        return redirect("submissions:detail", pk=submission.pk)
    return render(request, "submissions/form.html", {"form": form})


@login_required
def detail(request, pk):
    from repository_verification.services import has_current_verification
    submission = get_object_or_404(accessible(request.user).select_related("published_revision"), pk=pk)
    context = {
        "submission": submission, "revisions": submission.revisions.all(),
        "repository_verified": has_current_verification(submission),
        # Once this draft revision has been submitted, the page states the outcome instead of offering submission.
        "current_revision": submission.revisions.filter(number=submission.revision).first(),
        "publication_declaration": DECLARATION, "abstract_license": ABSTRACT_LICENSE,
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
        form = SubmissionForm(request.POST or None, instance=submission, user=request.user)
        if request.method == "POST" and form.is_valid():
            if form.cleaned_data["revision"] != submission.revision:
                form.add_error(None, "Someone saved a newer draft. Reload this page before editing so their changes are not overwritten.")
                return render(request, "submissions/form.html", {"form": form, "submission": submission}, status=409)
            submission = form.save(commit=False)
            submission.revision += 1
            submission.save()
            messages.success(request, "Draft changes saved. Published details stay unchanged until this revision is approved.")
            return redirect("submissions:detail", pk=submission.pk)
    return render(request, "submissions/form.html", {"form": form, "submission": submission})


@login_required
@require_POST
def submit(request, pk):
    with transaction.atomic():
        submission = get_object_or_404(accessible(request.user).select_for_update(), pk=pk)
        if request.POST.get("revision") != str(submission.revision):
            messages.error(request, "The draft changed. Please check the current details and submit again.")
            return redirect("submissions:detail", pk=pk)
        existing = submission.revisions.filter(number=submission.revision).first()
        if existing:
            if existing.status == "rejected":
                messages.info(request, "Please edit and save a new draft before resubmitting.")
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
            # The draft page itself states that the details were submitted; no message repeats it.
        elif revision.status == "rejected":
            messages.info(request, "Please edit and save a new draft before resubmitting.")
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


def archive_feed(request):
    from scripts.feed import render_feed
    response = HttpResponse(render_feed(public_archive()), content_type="application/atom+xml; charset=utf-8")
    response["Cache-Control"] = "public, max-age=60"
    return response
