import hashlib
import json
import re
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.middleware import limit_action

from .forms import SubmissionForm, review_form
from .models import EditorInvitation, Submission, SubmissionRevision
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
        "submission": submission, "revisions": submission.revisions.all(), "can_manage": owner, "editors": submission.editors.all(),
        "repository_verified": has_current_verification(submission),
        # Once this draft revision has been submitted, the page states the outcome instead of offering submission.
        "current_revision": submission.revisions.filter(number=submission.revision).first(),
    }
    if owner:
        context.update(invitations=submission.invitations.filter(expires_at__gt=timezone.now(), revoked_at__isnull=True, accepted_at__isnull=True), created_invitation_url=request.session.pop(f"created_invitation_url:{submission.pk}", None))
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


@login_required
@require_POST
def invite(request, pk):
    with transaction.atomic():
        submission = get_object_or_404(Submission.objects.select_for_update(), pk=pk, owner=request.user)
        limited = limit_action(request, action="submission_access_grant", user=request.user, key=str(submission.pk))
        if limited:
            return limited
        if submission.invitations.filter(expires_at__gt=timezone.now(), revoked_at__isnull=True, accepted_at__isnull=True).count() >= 10:
            messages.error(request, "There are already ten active invitations. Revoke an unused invitation first.")
        else:
            invitation, token = EditorInvitation.issue(submission)
            request.session[f"created_invitation_url:{submission.pk}"] = request.build_absolute_uri(reverse("submissions:accept_invitation", args=[token]))
            messages.success(request, "Private invitation created. Share it only with the person you want to grant editing access.")
    return redirect("submissions:detail", pk=pk)


@login_required
@require_POST
def revoke_invitation(request, pk, invitation_id):
    with transaction.atomic():
        submission = get_object_or_404(Submission.objects.select_for_update(), pk=pk, owner=request.user)
        get_object_or_404(submission.invitations, pk=invitation_id)
        limited = limit_action(request, action="submission_access_revoke", user=request.user, key=str(submission.pk), retry_after=300)
        if limited:
            return limited
        now = timezone.now()
        submission.invitations.filter(pk=invitation_id).update(revoked_at=now)
        Submission.objects.filter(pk=pk).update(updated_at=now)
    messages.success(request, "Invitation revoked.")
    return redirect("submissions:detail", pk=pk)


@login_required
@require_POST
def remove_editor(request, pk, user_id):
    with transaction.atomic():
        submission = get_object_or_404(Submission.objects.select_for_update(), pk=pk, owner=request.user)
        limited = limit_action(request, action="submission_access_revoke", user=request.user, key=str(submission.pk), retry_after=300)
        if limited:
            return limited
        submission.editors.remove(user_id)
        Submission.objects.filter(pk=pk).update(updated_at=timezone.now())
    messages.success(request, "Editing access removed.")
    return redirect("submissions:detail", pk=pk)


@login_required
def accept_invitation(request, token):
    if not re.fullmatch(r"[\w-]{43}", token, flags=re.ASCII):
        raise Http404
    with transaction.atomic():
        invitation = get_object_or_404(EditorInvitation.objects.select_for_update().select_related("submission"), token_hash=hashlib.sha256(token.encode()).hexdigest())
        if not invitation.is_active:
            raise Http404("This invitation has expired, been revoked or already been used.")
        if request.method == "POST":
            limited = limit_action(request, action="submission_accept_invitation", user=request.user)
            if limited:
                return limited
            if invitation.submission.owner_id != request.user.pk:
                invitation.submission.editors.add(request.user)
                invitation.accepted_at = timezone.now()
                invitation.accepted_by = request.user
                invitation.save(update_fields=["accepted_at", "accepted_by"])
                messages.success(request, "You now have editing access to this submission.")
            else:
                messages.info(request, "You already own this submission. Share the invitation with your collaborator.")
            return redirect("submissions:detail", pk=invitation.submission_id)
        response = render(request, "submissions/invitation.html", {"invitation": invitation, "submission": invitation.submission})
        response["Referrer-Policy"] = "same-origin"
        response["Cache-Control"] = "no-store"
        return response


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
