"""Private drafts and immutable, explicitly reviewed public revisions."""
import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone


CATEGORIES = [
    ("math.AG", "Algebraic geometry"), ("math.AP", "Analysis of PDEs"),
    ("math.AT", "Algebraic topology"), ("math.CO", "Combinatorics"),
    ("math.DG", "Differential geometry"), ("math.DS", "Dynamical systems"),
    ("math.FA", "Functional analysis"), ("math.LO", "Logic and foundations"),
    ("math.NT", "Number theory"), ("math.PR", "Probability"),
    ("math.GM", "General mathematics"),
]
KINDS = [(x, x) for x in ("Research", "Exposition", "Formalization", "Mixed")]
STAGES = [(x, x) for x in ("Exploration", "Partial results", "Claimed solution", "Ongoing", "Reference", "Archived")]
COLLABORATION = [(x, x) for x in ("Open to contributions", "Specific help wanted", "Currently closed", "See repository")]


class Submission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_submissions")
    editors = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="editable_submissions")
    title = models.CharField(max_length=120)
    repository_url = models.URLField(max_length=500, blank=True)
    reading_url = models.URLField(max_length=500, blank=True)
    abstract = models.TextField(max_length=1500, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORIES, blank=True)
    collaborators = models.JSONField(default=list, blank=True)
    kind = models.CharField(max_length=30, choices=KINDS, default="Research")
    stage = models.CharField(max_length=30, choices=STAGES, default="Exploration")
    collaboration = models.CharField(max_length=40, choices=COLLABORATION, default="See repository")
    contribution_note = models.TextField(max_length=1500, blank=True)
    license_note = models.TextField(max_length=1000, blank=True)
    permission_confirmed = models.BooleanField(default=False)
    license_confirmed = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    published_revision = models.ForeignKey("SubmissionRevision", null=True, blank=True, on_delete=models.PROTECT, related_name="published_for")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title

    @property
    def public_id(self):
        return f"project-{self.pk.hex}"

    @property
    def status(self):
        if self.revisions.filter(number=self.revision, status="pending").exists():
            return "Awaiting review"
        if self.published_revision_id:
            return "Published" if self.published_revision.number == self.revision else "Published; draft changes"
        latest = self.revisions.filter(number=self.revision).first()
        return "Changes requested" if latest and latest.status == "rejected" else "Draft"

    def can_edit(self, user):
        return user.is_authenticated and (self.owner_id == user.pk or self.editors.filter(pk=user.pk).exists())

    def snapshot(self):
        return {name: getattr(self, name) for name in (
            "title", "repository_url", "reading_url", "abstract", "category", "collaborators",
            "kind", "stage", "collaboration", "contribution_note", "license_note",
        )}


class SubmissionRevision(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    snapshot = models.JSONField(editable=False)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="submitted_revisions")
    status = models.CharField(max_length=12, choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Changes requested")], default="pending")
    review_note = models.TextField(max_length=3000, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_revisions")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["submission", "number"], name="unique_submission_revision")]

    def __str__(self):
        return f"{self.submission.title} — revision {self.number}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if (self.snapshot != previous.snapshot or self.number != previous.number or self.submission_id != previous.submission_id or self.submitted_by_id != previous.submitted_by_id):
                raise ValidationError("A submitted revision is immutable. Edit the draft and submit a new revision.")
        super().save(*args, **kwargs)

    def review(self, reviewer, *, approve, note=""):
        if not reviewer.is_active or not reviewer.is_staff or not reviewer.has_perm("submissions.change_submissionrevision"):
            raise ValidationError("Only an authorised reviewer can publish a submission.")
        with transaction.atomic():
            submission = Submission.objects.select_for_update().get(pk=self.submission_id)
            revision = type(self).objects.select_for_update().get(pk=self.pk)
            if revision.status != "pending":
                raise ValidationError("This revision has already been reviewed.")
            if approve and (reviewer.pk in {submission.owner_id, revision.submitted_by_id} or submission.editors.filter(pk=reviewer.pk).exists()):
                raise ValidationError("You cannot approve a submission you own, edit or submitted. Ask another reviewer to publish it.")
            if approve and submission.published_revision_id and submission.published_revision.number >= revision.number:
                raise ValidationError("A newer revision is already published. This review cannot replace it.")
            if approve:
                from repository_verification.services import require_verified
                if submission.repository_url != revision.snapshot["repository_url"]:
                    raise ValidationError("The repository changed after this revision was submitted. Submit the new draft for review and verify that repository first.")
                require_verified(submission, actor=reviewer)
                # Validate the frozen material, never the author's potentially newer draft.
                from .forms import review_form
                candidate = Submission(**revision.snapshot, permission_confirmed=True, license_confirmed=True)
                form = review_form(candidate)
                if not form.is_valid():
                    raise ValidationError("The submitted revision is incomplete or invalid and cannot be published.")
                from scripts.validate import ArchiveError, validate_archive
                try:
                    validate_archive({"version": 1, "projects": [revision.public_project()]})
                except ArchiveError as error:
                    raise ValidationError(f"The submitted revision cannot be exported: {error}") from error
            revision.status = "approved" if approve else "rejected"
            revision.review_note = note
            revision.reviewed_by = reviewer
            revision.reviewed_at = timezone.now()
            revision.save(update_fields=["status", "review_note", "reviewed_by", "reviewed_at"])
            if approve:
                submission.published_revision = revision
                submission.save(update_fields=["published_revision"])
        self.refresh_from_db()

    def public_project(self):
        """Explicit allowlist: never serialize owners, emails, permissions or review notes."""
        data = self.snapshot
        first = self.submission.revisions.filter(status="approved").order_by("reviewed_at").first()
        return {
            "id": self.submission.public_id, "title": data["title"],
            "summary": data["abstract"], "description": data["abstract"],
            "categories": [{"code": data["category"], "label": dict(CATEGORIES)[data["category"]]}],
            "kind": data["kind"], "stage": data["stage"], "collaboration": data["collaboration"],
            "repositoryUrl": data["repository_url"], "readingUrl": data["reading_url"],
            "credits": [
                {"name": author["name"], "role": author["role"], **{key: author[key] for key in ("orcid", "affiliation") if author.get(key)}}
                for author in data["collaborators"]
            ],
            "added": (first.reviewed_at if first else self.reviewed_at or timezone.now()).date().isoformat(),
            "example": False, "highlights": ["Follow the project website for the research and its latest version."],
            "contributionNote": data["contribution_note"], "licenseNote": data["license_note"],
        }


class EditorInvitation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="invitations")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    @classmethod
    def issue(cls, submission):
        token = secrets.token_urlsafe(32)
        invitation = cls.objects.create(submission=submission, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=timezone.now() + timedelta(days=7))
        return invitation, token

    @property
    def is_active(self):
        return not self.revoked_at and not self.accepted_at and self.expires_at > timezone.now()
