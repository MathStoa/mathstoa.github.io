"""Approved account and unused-draft retention; submitted records are protected."""
from datetime import timedelta

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.db.models import Max, Q
from django.db.models.deletion import ProtectedError, RestrictedError

from repository_verification.models import RepositoryVerification
from submissions.models import Submission


UNVERIFIED_DAYS = 7
DRAFT_DAYS = 90
INACTIVE_ACCOUNT_DAYS = 365
AUTH_RECORD_APPS = {"account", "socialaccount", "mfa", "usersessions"}


def _lock(queryset):
    # Leave records currently being edited for a later cleanup run.
    options = {"skip_locked": True} if connections[queryset.db].features.has_select_for_update_skip_locked else {}
    return queryset.select_for_update(**options)


def _has_responsibilities(user):
    if user.is_staff or user.is_superuser or user.groups.exists() or user.user_permissions.exists():
        return True
    # Authentication records disappear with the account. Any other reference,
    # including a future application relation, conservatively protects it.
    for relation in user._meta.related_objects:
        model = relation.related_model
        if model._meta.app_label in AUTH_RECORD_APPS:
            continue
        if model._default_manager.filter(**{relation.field.name: user}).exists():
            return True
    return False


def _account_expiry(user, now):
    if _has_responsibilities(user):
        return None
    if (user.date_joined < now - timedelta(days=UNVERIFIED_DAYS)
            and user.last_login is None
            and not EmailAddress.objects.filter(user=user, verified=True).exists()
            and not SocialAccount.objects.filter(user=user).exists()):
        return "unverified_accounts"
    last_login = user.last_login or user.date_joined
    if max(user.date_joined, last_login) < now - timedelta(days=INACTIVE_ACCOUNT_DAYS):
        return "inactive_accounts"
    return None


def _purge_account(pk, *, now, dry_run):
    with transaction.atomic():
        user = _lock(get_user_model().objects).filter(pk=pk).first()
        if user is None:
            return None
        category = _account_expiry(user, now)
        if category and not dry_run:
            user.delete()
        return category


def _draft_activity(submission):
    dates = [submission.created_at, submission.updated_at]
    for records, fields in (
        (submission.invitations, ("created_at", "accepted_at", "revoked_at")),
        (submission.verification_attempts, ("created_at", "consumed_at", "revoked_at")),
        (submission.repository_verifications, ("verified_at", "revoked_at")),
    ):
        dates.extend(value for value in records.aggregate(**{field: Max(field) for field in fields}).values() if value is not None)
    return max(dates)


def _purge_draft(pk, *, now, dry_run):
    with transaction.atomic():
        submission = _lock(Submission.objects).filter(pk=pk).first()
        if submission is None or submission.published_revision_id or submission.revisions.exists():
            return 0, 0
        activity = _draft_activity(submission)
        if not dry_run and activity > submission.updated_at:
            # Preserve real access/verification activity after its short-lived
            # invitation or attempt record is pruned later in this same run.
            Submission.objects.filter(pk=pk).update(updated_at=activity)
        if activity >= now - timedelta(days=DRAFT_DAYS):
            return 0, 0
        if submission.invitations.filter(expires_at__gt=now, accepted_at__isnull=True, revoked_at__isnull=True).exists():
            return 0, 0
        if submission.verification_attempts.filter(expires_at__gt=now, consumed_at__isnull=True, revoked_at__isnull=True).exists():
            return 0, 0
        proofs = RepositoryVerification.objects.filter(submission=submission)
        if proofs.filter(valid_until__gt=now).exists():
            return 0, 0
        proof_count = proofs.count()
        if not dry_run:
            # These proofs have no submitted/public scholarly record. Remove
            # them first because their attempt relation deliberately PROTECTs.
            proofs.delete()
            submission.delete()
        return 1, proof_count


def purge_retained_data(*, now, dry_run=False):
    counts = {"unverified_accounts": 0, "inactive_accounts": 0, "unused_drafts": 0, "draft_verifications": 0}
    User = get_user_model()
    candidates = User.objects.filter(is_staff=False, is_superuser=False).filter(
        Q(date_joined__lt=now - timedelta(days=UNVERIFIED_DAYS), last_login__isnull=True)
        | (Q(date_joined__lt=now - timedelta(days=INACTIVE_ACCOUNT_DAYS))
           & (Q(last_login__lt=now - timedelta(days=INACTIVE_ACCOUNT_DAYS)) | Q(last_login__isnull=True)))
    )
    # Accounts are checked first: dry-run and deletion see the same existing
    # responsibilities. An account freed by draft deletion waits for the next run.
    for pk in candidates.values_list("pk", flat=True).iterator(chunk_size=200):
        try:
            category = _purge_account(pk, now=now, dry_run=dry_run)
        except (ProtectedError, RestrictedError):
            continue
        if category:
            counts[category] += 1
    # Refresh retained activity even on younger drafts before transient cleanup
    # can remove the invitation/verification timestamps that explain it.
    candidates = Submission.objects.filter(published_revision__isnull=True, revisions__isnull=True)
    for pk in candidates.values_list("pk", flat=True).iterator(chunk_size=200):
        try:
            drafts, proofs = _purge_draft(pk, now=now, dry_run=dry_run)
        except (ProtectedError, RestrictedError):
            continue
        counts["unused_drafts"] += drafts
        counts["draft_verifications"] += proofs
    return counts
