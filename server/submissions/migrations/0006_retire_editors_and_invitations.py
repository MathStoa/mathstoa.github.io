"""Drop the editor memberships and invitations left dormant when editing access was withdrawn on 2026-11-03.

Written by hand around the operations makemigrations generated (RemoveField and DeleteModel).
"""

from django.db import migrations
from django.db.models import Q
from django.utils import timezone


def delete_ended_invitations_and_refuse_live_rows(apps, schema_editor):
    """Invitations that were revoked, used or have expired are deleted here, as the retired hourly purge did.

    An invitation still open, or an editor membership, is never dropped unnoticed: the migration refuses,
    so inspect and delete those rows deliberately with the previous release, then migrate again.
    """
    Submission = apps.get_model("submissions", "Submission")
    EditorInvitation = apps.get_model("submissions", "EditorInvitation")
    EditorInvitation.objects.filter(Q(revoked_at__isnull=False) | Q(accepted_at__isnull=False) | Q(expires_at__lte=timezone.now())).delete()
    counts = {
        "editor memberships in submissions_submission_editors": Submission.editors.through.objects.count(),
        "open invitations in submissions_editorinvitation": EditorInvitation.objects.count(),
    }
    if any(counts.values()):
        raise RuntimeError(
            "Refusing to drop tables that still hold live rows: "
            + ", ".join(f"{count} {name}" for name, count in counts.items())
            + ". Inspect and delete them deliberately, then run migrate again."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0005_moderation_labels"),
    ]

    operations = [
        migrations.RunPython(delete_ended_invitations_and_refuse_live_rows, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="submission",
            name="editors",
        ),
        migrations.DeleteModel(
            name="EditorInvitation",
        ),
    ]
