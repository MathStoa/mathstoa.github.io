"""Moderator-only removal of an organisation and its login, without project erasure."""
from allauth.account.utils import user_pk_to_url_str
from allauth.account.internal.flows.login import _AUTHENTICATION_METHODS_USER_SESSION_KEY
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError, RestrictedError
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from repository_verification.models import RepositoryVerification
from submissions.models import Submission, SubmissionRevision

from .models import OrganisationAccount
from .sessions import SessionStore


DELETE_PERMISSIONS = ("accounts.change_organisationaccount", "accounts.delete_organisationaccount")


def can_delete_organisation(actor):
    return bool(
        actor is not None and actor.is_authenticated and actor.is_active and actor.is_staff
        and actor.has_perms(DELETE_PERMISSIONS)
    )


def organisation_deletion_blockers(organisation):
    """Describe retained responsibilities using current records, including on POST."""
    current = OrganisationAccount.objects.select_related("user").filter(pk=organisation.pk).first()
    if current is None:
        return ["This organisation account has already been deleted."]
    user = current.user
    blockers = []
    if user.is_staff or user.is_superuser:
        blockers.append("This account has staff privileges and cannot be deleted as an organisation.")
    if user.groups.exists() or user.user_permissions.exists():
        blockers.append("This account has assigned roles or permissions. Resolve those privileges before deleting it as an organisation.")
    if Submission.objects.filter(owner_id=user.pk).exists():
        blockers.append("This organisation owns project submissions. Resolve their ownership before deleting the account; projects will not be deleted.")
    if RepositoryVerification.objects.filter(Q(user_id=user.pk) | Q(attempt__user_id=user.pk)).exists():
        blockers.append("This account is linked to retained repository-verification evidence. Resolve that evidence before deleting the account.")
    if (
        LogEntry.objects.filter(user_id=user.pk).exists()
        or SubmissionRevision.objects.filter(reviewed_by_id=user.pk).exists()
        or OrganisationAccount.objects.filter(reviewed_by_id=user.pk).exists()
    ):
        blockers.append("This account has administration or moderation history that requires manual review before deletion.")
    if current.notification_status == "sending":
        blockers.append("A registration-decision email is being sent. Wait for delivery to finish, then reload this page.")
    return blockers


@sensitive_variables()
def _delete_active_sessions(user):
    """Use the encrypted session decoder; never log payloads or another user's data."""
    user_id = str(user.pk)
    pending_id = user_pk_to_url_str(user)
    decoder = SessionStore()
    keys = []
    deleted = 0
    sessions = Session.objects.filter(expire_date__gt=timezone.now()).only("session_key", "session_data")
    for session in sessions.iterator(chunk_size=200):
        data = decoder.decode(session.session_data)
        pending = data.get("account_login")
        matches = (
            data.get("_auth_user_id") == user_id
            or data.get(_AUTHENTICATION_METHODS_USER_SESSION_KEY) == user_id
            or (isinstance(pending, dict) and pending.get("user_pk") == pending_id)
        )
        if matches:
            keys.append(session.pk)
        if len(keys) == 200:
            deleted += Session.objects.filter(pk__in=keys).delete()[0]
            keys.clear()
    if keys:
        deleted += Session.objects.filter(pk__in=keys).delete()[0]
    return deleted


def _redact_target_logs(organisation_id, user_id):
    for model, pk, label in (
        (OrganisationAccount, organisation_id, f"Organisation account #{organisation_id}"),
        (get_user_model(), user_id, f"Deleted organisation login #{user_id}"),
    ):
        content_type = ContentType.objects.get_for_model(model)
        records = LogEntry.objects.filter(content_type=content_type, object_id=str(pk))
        # The admin's fresh confirmation audit already contains only the ID.
        records.exclude(action_flag=DELETION, object_repr=label, change_message="").update(
            object_repr=label, change_message="",
        )


def delete_organisation(organisation, *, moderator):
    """Delete both records in one transaction, or retain everything on refusal."""
    User = get_user_model()
    actor = User.objects.filter(pk=getattr(moderator, "pk", None)).first()
    # A fresh instance avoids cached permissions surviving a role revocation.
    if not can_delete_organisation(actor):
        raise ValidationError("Only an authorised moderator with organisation deletion permission can delete this account.")
    try:
        with transaction.atomic():
            # Same order as moderation: organisation, then its underlying login.
            current = OrganisationAccount.objects.select_for_update().get(pk=organisation.pk)
            user = User.objects.select_for_update().get(pk=current.user_id)
            if user.pk == actor.pk:
                raise ValidationError("You cannot delete the organisation account you are currently using.")
            blockers = organisation_deletion_blockers(current)
            if blockers:
                raise ValidationError(blockers)
            organisation_id, user_id = current.pk, user.pk
            sessions_deleted = _delete_active_sessions(user)
            _redact_target_logs(organisation_id, user_id)
            # Removing only the organisation row would leave an ungated personal
            # login. Never commit that intermediate state.
            current.delete()
            user.delete()
    except (OrganisationAccount.DoesNotExist, User.DoesNotExist) as error:
        raise ValidationError("This organisation account has already been deleted. Reload the moderation queue.") from error
    except (ProtectedError, RestrictedError, IntegrityError) as error:
        raise ValidationError("This account has retained records that prevent deletion. Resolve those records and try again; nothing has been deleted.") from error
    return {"organisation_id": organisation_id, "user_id": user_id, "sessions_deleted": sessions_deleted}
