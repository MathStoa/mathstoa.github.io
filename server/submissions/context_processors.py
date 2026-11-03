"""Size of the moderation queue for moderators' navigation; nothing for anyone else."""
from .models import SubmissionRevision


def review_queue(request):
    user = getattr(request, "user", None)
    if not (
        user is not None and user.is_authenticated and user.is_active and user.is_staff
        and user.has_perm("submissions.change_submissionrevision")
    ):
        return {"pending_reviews": None}
    return {"pending_reviews": SubmissionRevision.objects.filter(status="pending").count()}
