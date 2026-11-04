"""Private account applications and explicit moderator decisions."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import Lower
from django.utils import timezone


class AccountApproval(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account_approval")
    status = models.CharField(max_length=12, default="pending", choices=[("pending", "Awaiting approval"), ("approved", "Approved"), ("rejected", "Rejected")])
    review_note = models.TextField("decision note", max_length=3000, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_account_applications")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "account application"

    def __str__(self):
        return str(self.user)

    def review(self, reviewer, *, approve, note=""):
        from allauth.account.models import EmailAddress
        from allauth.socialaccount.models import SocialAccount

        if not reviewer.is_active or not reviewer.is_staff or not reviewer.has_perm("accounts.change_accountapproval"):
            raise ValidationError("Only an authorised moderator can review an account application.")
        note = note.strip()
        if len(note) > 3000:
            raise ValidationError("The decision note must be at most 3000 characters.")
        with transaction.atomic():
            application = type(self).objects.select_for_update().get(pk=self.pk)
            if reviewer.pk == application.user_id:
                raise ValidationError("You cannot approve your own account application.")
            if application.status != "pending":
                raise ValidationError("This application has already been moderated.")
            if approve and not (
                EmailAddress.objects.filter(user_id=application.user_id, verified=True).exists()
                or SocialAccount.objects.filter(user_id=application.user_id).exists()
            ):
                raise ValidationError("The applicant must verify their email or sign in with a provider before approval.")
            application.status = "approved" if approve else "rejected"
            application.review_note = note
            application.reviewed_by = reviewer
            application.reviewed_at = timezone.now()
            application.save(update_fields=["status", "review_note", "reviewed_by", "reviewed_at"])
        self.refresh_from_db()


class OrganisationAccount(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="organisation_account")
    name = models.CharField("organisation name", max_length=150)
    website = models.URLField(max_length=500)
    description = models.TextField(max_length=1500)
    email = models.EmailField("organisation email", max_length=254)
    representative_name = models.CharField("your name", max_length=150)
    authority = models.TextField("your role and authority to represent the organisation", max_length=1500)
    status = models.CharField(max_length=12, default="pending", choices=[("pending", "Awaiting moderation"), ("approved", "Approved"), ("rejected", "Rejected")])
    review_note = models.TextField("decision message to applicant", max_length=3000, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_organisations")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    notification_status = models.CharField(max_length=10, blank=True, choices=[(value, value.title()) for value in ("queued", "sending", "sent", "failed")])
    notification_attempted_at = models.DateTimeField(null=True, blank=True)
    decision_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "organisation account"
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_organisation_email")]

    def __str__(self):
        return self.name

    def review(self, reviewer, *, approve, note=""):
        from allauth.account.models import EmailAddress
        from django.contrib.auth import get_user_model

        if not reviewer.is_active or not reviewer.is_staff or not reviewer.has_perm("accounts.change_organisationaccount"):
            raise ValidationError("Only an authorised moderator can review an organisation.")
        note = note.strip()
        if len(note) > 3000:
            raise ValidationError("The decision message must be at most 3000 characters.")
        if not approve and not note:
            raise ValidationError("Enter a decision message before rejecting an application.")
        with transaction.atomic():
            application = type(self).objects.select_for_update().get(pk=self.pk)
            user = get_user_model().objects.select_for_update().get(pk=application.user_id)
            if reviewer.pk == user.pk:
                raise ValidationError("An organisation cannot approve its own application.")
            if application.status != "pending":
                raise ValidationError("This application has already been moderated.")
            if approve:
                addresses = EmailAddress.objects.filter(email__iexact=application.email)
                if (not addresses.filter(user=user, primary=True).exists()
                        or addresses.exclude(user=user).exists()
                        or get_user_model().objects.filter(email__iexact=application.email).exclude(pk=user.pk).exists()
                        or user.email.casefold() != application.email.casefold()):
                    raise ValidationError("The registered email is missing or belongs to another account. Resolve this before approval.")
                if user.is_staff or user.is_superuser:
                    raise ValidationError("Organisation accounts cannot have staff privileges.")
            application.status = "approved" if approve else "rejected"
            application.review_note = note
            application.reviewed_by = reviewer
            application.reviewed_at = timezone.now()
            application.notification_status = "queued"
            application.save(update_fields=["status", "review_note", "reviewed_by", "reviewed_at", "notification_status"])
            user.is_active = approve
            user.save(update_fields=["is_active"])
        self.refresh_from_db()
