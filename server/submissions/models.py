"""Private submissions and immutable, explicitly moderated public revisions."""
import uuid

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
    ("cs.LO", "Lean formalisation"), ("math.GM", "General mathematics"),
]
# A project names one main subject and, if it spans several areas, up to this many further subjects.
MAX_SECONDARY_CATEGORIES = 3


def display_name(name):
    """Contributors enter "First names, surname"; the public sees "First names surname"."""
    first, comma, surname = name.partition(",")
    return f"{first.strip()} {surname.strip()}" if comma else name


# Submitted revisions freeze these fields. Older snapshots may hold fields that have since been removed.
SNAPSHOT_FIELDS = ("title", "repository_url", "doi", "reading_url", "abstract", "category", "secondary_categories", "collaborators")


class Submission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_submissions")
    title = models.CharField(max_length=120)
    repository_url = models.URLField(max_length=500, blank=True)
    doi = models.CharField("DOI", max_length=300, blank=True)
    reading_url = models.URLField(max_length=500, blank=True)
    abstract = models.TextField(max_length=1500, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORIES, blank=True)
    # Further subject codes from CATEGORIES; SubmissionForm keeps them valid and distinct from the main subject.
    secondary_categories = models.JSONField(default=list, blank=True)
    collaborators = models.JSONField(default=list, blank=True)
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
        latest = self.revisions.filter(number=self.revision).first()
        if latest and latest.status == "unverified":
            return "Awaiting repository verification"
        if latest and latest.status == "pending":
            return "Awaiting moderation"
        if self.published_revision_id:
            if self.published_revision.number == self.revision:
                return "Published"
            return "Published; changes requested" if latest and latest.status == "rejected" else "Published; incomplete changes"
        return "Changes requested" if latest and latest.status == "rejected" else "Incomplete submission"

    @property
    def secondary_category_labels(self):
        labels = dict(CATEGORIES)
        return [labels.get(code, code) for code in self.secondary_categories]

    def snapshot(self):
        return {name: getattr(self, name) for name in SNAPSHOT_FIELDS}


class SubmissionRevision(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    snapshot = models.JSONField(editable=False)
    # At most four notice kinds × 100 contributor digests; no addresses or message copies.
    notification_keys = models.JSONField(default=list, editable=False)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="submitted_revisions")
    status = models.CharField(max_length=12, choices=[("unverified", "Awaiting repository verification"), ("pending", "Pending"), ("approved", "Approved"), ("rejected", "Changes requested"), ("superseded", "Replaced by a newer revision")], default="pending")
    review_note = models.TextField("moderation note", max_length=3000, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviewed_revisions", verbose_name="moderated by")
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField("moderated at", null=True, blank=True)

    class Meta:
        ordering = ["-number"]
        constraints = [models.UniqueConstraint(fields=["submission", "number"], name="unique_submission_revision")]

    def __str__(self):
        return f"{self.submission.title} — revision {self.number}"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            if (self.snapshot != previous.snapshot or self.number != previous.number or self.submission_id != previous.submission_id or self.submitted_by_id != previous.submitted_by_id):
                raise ValidationError("A submitted revision is immutable. Edit the submission and submit a new revision.")
        super().save(*args, **kwargs)

    def review(self, reviewer, *, approve, note=""):
        if not reviewer.is_active or not reviewer.is_staff or not reviewer.has_perm("submissions.change_submissionrevision"):
            raise ValidationError("Only an authorised moderator can publish a submission.")
        with transaction.atomic():
            submission = Submission.objects.select_for_update().get(pk=self.submission_id)
            revision = type(self).objects.select_for_update().get(pk=self.pk)
            if revision.status == "unverified":
                raise ValidationError("Verify repository authorization before this submission can be moderated.")
            if revision.status != "pending":
                raise ValidationError("This revision has already been moderated.")
            # An authorised moderator may approve a revision they own or submitted; reviewed_by records who decided.
            if approve and submission.published_revision_id and submission.published_revision.number >= revision.number:
                raise ValidationError("A newer revision is already published. This decision cannot replace it.")
            if approve:
                from repository_verification.services import require_verified
                if submission.repository_url != revision.snapshot["repository_url"]:
                    raise ValidationError("The repository changed after this revision was submitted. Submit the updated details and verify that repository first.")
                require_verified(submission, actor=reviewer)
                # Validate the frozen material, never the author's potentially newer draft.
                from .forms import review_form
                frozen = {name: revision.snapshot[name] for name in SNAPSHOT_FIELDS if name in revision.snapshot}
                candidate = Submission(**frozen, permission_confirmed=True, license_confirmed=True)
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
            else:
                from .notifications import notify_changes_requested
                # Persist the outbox in this decision transaction; rollback cancels both.
                notify_changes_requested(revision.pk)
        self.refresh_from_db()

    def public_project(self):
        """Explicit allowlist: never serialize owners, emails, permissions or moderation notes."""
        data = self.snapshot
        first = self.submission.revisions.filter(status="approved").order_by("reviewed_at").first()
        labels = dict(CATEGORIES)
        # The main subject comes first. Revisions frozen before further subjects existed have none.
        codes = dict.fromkeys([data["category"], *data.get("secondary_categories", [])])
        from .publication import ABSTRACT_LICENSE, authenticated_orcid
        declaration = data.get("publication_declaration", {})
        # Do not retroactively assert a licence grant for a historical revision
        # whose snapshot predates recorded publication declarations.
        licensing = {}
        if declaration.get("license") == ABSTRACT_LICENSE and declaration.get("accepted_at") and declaration.get("version"):
            licensing = {"summaryLicense": ABSTRACT_LICENSE, "summaryAttribution": [display_name(author["name"]) for author in data["collaborators"]]}
        return {
            "id": self.submission.public_id, "title": data["title"],
            "summary": data["abstract"],
            **licensing,
            "categories": [{"code": code, "label": labels[code]} for code in codes],
            "repositoryUrl": data["repository_url"],
            # The DOI and website are optional.
            **{key: data[name] for key, name in (("doi", "doi"), ("readingUrl", "reading_url")) if data.get(name)},
            "credits": [
                {"name": display_name(author["name"]), **({"orcid": author["orcid"], "orcidAuthenticated": authenticated_orcid(author)} if author.get("orcid") else {})}
                for author in data["collaborators"]
            ],
            "added": (first.reviewed_at if first else self.reviewed_at or timezone.now()).date().isoformat(),
            "example": False,
        }


class NotificationQueueLock(models.Model):
    """One database row serializes quota reservations across web and worker processes."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    version = models.PositiveBigIntegerField(default=0)


class NotificationDelivery(models.Model):
    """Bounded, private outbox; resolve the address from the current revision at send time."""
    revision = models.ForeignKey(SubmissionRevision, on_delete=models.CASCADE, related_name="notification_deliveries")
    account = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="notification_deliveries")
    kind = models.CharField(max_length=12, choices=[("created", "Submission created"), ("updated", "Submission updated"), ("confirmation", "Legacy submission confirmation"), ("changes", "Changes requested")])
    recipient_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=12, default="queued", choices=[(value, value.title()) for value in ("queued", "sending", "sent", "failed", "suppressed", "cancelled", "expired")])
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    attempted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["revision", "kind", "recipient_digest"], name="unique_revision_notice_recipient")]
        indexes = [models.Index(fields=["status", "created_at"], name="notification_pending"), models.Index(fields=["recipient_digest", "created_at"], name="notification_recipient")]
