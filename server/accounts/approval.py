"""Beta admission applies to new personal accounts, across all sign-in methods."""
from django.conf import settings

from .models import AccountApproval


def register_for_approval(user):
    # Called only while saving a new allauth account. Existing members, account
    # connections and the separately moderated organisation flow are untouched.
    if settings.ACCOUNT_APPROVAL_REQUIRED:
        AccountApproval.objects.get_or_create(user=user)


def denied_approval_status(user):
    if user is None or not user.is_authenticated or not user.pk:
        return None
    # Deliberately independent of the rollout setting: turning off moderation
    # for future signups must not silently admit pending or rejected applicants.
    return AccountApproval.objects.filter(user_id=user.pk).exclude(status="approved").values_list("status", flat=True).first()


def approval_denied_response(request, user):
    from allauth.account.internal.flows.login import clear_authentication_records
    from allauth.account.internal.stagekit import clear_login
    from django.shortcuts import render

    status = denied_approval_status(user)
    if status is None:
        return None
    clear_login(request)
    clear_authentication_records(request)
    return render(request, "account/approval_required.html", {"account_approval_status": status}, status=403)
