"""Use allauth's state/PKCE validation without retaining GitHub OAuth tokens."""

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.signals import pre_social_login
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.dispatch import receiver
from django.db.models.signals import post_delete, post_save, pre_save
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from submissions.models import Submission

from .github import GitHubError
from .models import RepositoryVerification, VerificationAttempt
from .services import check_github


def _revoke_for_user(user_id):
    now = timezone.now()
    VerificationAttempt.objects.filter(user_id=user_id, revoked_at__isnull=True).update(revoked_at=now)
    RepositoryVerification.objects.filter(user_id=user_id, revoked_at__isnull=True).update(revoked_at=now)


@receiver(pre_save, sender=Submission, dispatch_uid="mathstoa_verification_submission_changed")
def invalidate_changed_submission(sender, instance, update_fields=None, **kwargs):
    if instance._state.adding:
        return
    previous = sender.objects.filter(pk=instance.pk).values("repository_url", "owner_id").first()
    if not previous:
        return
    repository_changed = (update_fields is None or "repository_url" in update_fields) and previous["repository_url"] != instance.repository_url
    owner_changed = (update_fields is None or bool({"owner", "owner_id"}.intersection(update_fields))) and previous["owner_id"] != instance.owner_id
    if repository_changed or owner_changed:
        now = timezone.now()
        VerificationAttempt.objects.filter(submission_id=instance.pk, revoked_at__isnull=True).update(revoked_at=now)
        RepositoryVerification.objects.filter(submission_id=instance.pk, revoked_at__isnull=True).update(revoked_at=now)


@receiver(pre_save, sender=SocialAccount, dispatch_uid="mathstoa_verification_orcid_changed")
def invalidate_changed_orcid(sender, instance, update_fields=None, **kwargs):
    if instance._state.adding:
        return
    previous = sender.objects.filter(pk=instance.pk).values("provider", "uid", "user_id").first()
    if not previous:
        return
    changed = any(
        previous[field] != getattr(instance, field)
        and (update_fields is None or field in update_fields or (field == "user_id" and "user" in update_fields))
        for field in ("provider", "uid", "user_id")
    )
    if changed:
        if previous["provider"] == "orcid":
            _revoke_for_user(previous["user_id"])
        if instance.provider == "orcid":
            _revoke_for_user(instance.user_id)


@receiver(post_save, sender=SocialAccount, dispatch_uid="mathstoa_verification_orcid_added")
def invalidate_added_orcid(sender, instance, created, **kwargs):
    if created and instance.provider == "orcid":
        _revoke_for_user(instance.user_id)


@receiver(post_delete, sender=SocialAccount, dispatch_uid="mathstoa_verification_orcid_removed")
def invalidate_removed_orcid(sender, instance, **kwargs):
    if instance.provider == "orcid":
        _revoke_for_user(instance.user_id)


@receiver(pre_social_login, dispatch_uid="mathstoa_repository_verification_callback")
@sensitive_variables("sociallogin", "token", "data")
def verify_github_callback(sender, request, sociallogin, **kwargs):
    state = sociallogin.state or {}
    data = state.get("data")
    if not isinstance(data, dict) or "mathstoa_verification" not in data:
        return  # Ordinary sign-in and account connections keep allauth's flow.

    target = redirect("submissions:list")
    try:
        if (
            not request.user.is_authenticated
            or not request.user.is_active
            or sociallogin.account.provider != "github"
            or state.get("process") != "connect"
        ):
            raise ValidationError("Sign in to the account that started verification and try again.")
        attempt = VerificationAttempt.objects.select_related("submission").filter(
            pk=data.get("mathstoa_verification"), user=request.user, method="github",
            submission__owner=request.user,
        ).first()
        if not attempt:
            raise ValidationError("This verification request is no longer available. Start again from your submission.")
        target = redirect("verification:github_overview", pk=attempt.submission_id)
        github_uid = str(sociallogin.account.uid)
        if SocialAccount.objects.filter(provider="github", uid=github_uid).exclude(user=request.user).exists():
            raise ValidationError("This GitHub account is connected to another mathΣτοά account. Use that account or verify by file.")
        token = sociallogin.token
        if token is None or not token.token:
            raise ValidationError("GitHub did not return an authorization credential. Please try again.")
        check_github(
            attempt, request.user, data.get("nonce", ""), token.token,
            expected_github_uid=github_uid,
        )
        messages.success(request, "Repository authorization verified through GitHub. Any submitted revision awaiting this check is now ready for moderation.")
        target = redirect("submissions:detail", pk=attempt.submission_id)
    except (ValidationError, GitHubError, ValueError) as error:
        text = " ".join(error.messages) if isinstance(error, ValidationError) else (
            str(error) if isinstance(error, GitHubError) else "This verification request is invalid. Please start again."
        )
        messages.error(request, text)

    # Stop before allauth's sign-in/link stage. The proof retains the GitHub ID;
    # the OAuth token is used only in this callback and is never stored by us.
    raise ImmediateHttpResponse(target)
