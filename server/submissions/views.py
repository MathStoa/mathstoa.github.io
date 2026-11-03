import json
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.middleware import limit_action

from .forms import SubmissionForm, review_form
from .models import Submission, SubmissionRevision
from .notifications import notify_contributors, notify_moderators


def accessible(user):
    return Submission.objects.filter(Q(owner=user) | Q(editors=user)).distinct()


@login_required
def submission_list(request):
    return render(request, "submissions/list.html", {"submissions": accessible(request.user).select_related("published_revision")})


@login_required
def create(request):
    if request.method == "POST":
        limited = limit_action(request, action="submission_create", user=request.user, retry_after=300)
        if limited:
            return limited
    form = SubmissionForm(request.POST or None)
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
    owner = submission.owner_id == request.user.pk
    context = {
        "submission": submission, "revisions": submission.revisions.all(), "can_manage": owner,
        "repository_verified": has_current_verification(submission),
        # Once this draft revision has been submitted, the page states the outcome instead of offering submission.
        "current_revision": submission.revisions.filter(number=submission.revision).first(),
    }
    return render(request, "submissions/detail.html", context)


@login_required
def edit(request, pk):
    with transaction.atomic():
        submission = get_object_or_404(Submission.objects.select_for_update(), pk=pk)
        if not submission.can_edit(request.user):
            raise Http404
        if request.method == "POST":
            limited = limit_action(request, action="submission_edit", user=request.user, key=str(submission.pk), retry_after=300)
            if limited:
                return limited
        form = SubmissionForm(request.POST or None, instance=submission)
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
        submission = get_object_or_404(Submission.objects.select_for_update(), pk=pk)
        if not submission.can_edit(request.user):
            raise Http404
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
        form = review_form(submission)
        if not form.is_valid():
            messages.error(request, "Please complete the highlighted details before submitting.")
            return render(request, "submissions/form.html", {"form": form, "submission": submission}, status=400)
        from repository_verification.services import require_verified
        try:
            require_verified(submission, actor=request.user)
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
            return redirect("submissions:detail", pk=pk)
        revision, created = SubmissionRevision.objects.get_or_create(submission=submission, number=submission.revision, defaults={"snapshot": submission.snapshot(), "submitted_by": request.user})
        if created:
            transaction.on_commit(lambda: notify_moderators(revision))
            transaction.on_commit(lambda: notify_contributors(revision))
            # The draft page itself states that the details were submitted; no message repeats it.
        elif revision.status == "rejected":
            messages.info(request, "Please edit and save a new draft before resubmitting.")
        else:
            messages.info(request, "This revision has already been submitted.")
    return redirect("submissions:detail", pk=pk)


def public_archive():
    path = Path(getattr(settings, "ARCHIVE_PATH", Path(__file__).resolve().parents[2] / "docs/data/projects.json"))
    archive = json.loads(path.read_text(encoding="utf-8"))
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
