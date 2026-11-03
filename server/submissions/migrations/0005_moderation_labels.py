# Labels only: the admin calls these fields moderation, not review. No schema change.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("submissions", "0004_secondary_categories"),
    ]

    operations = [
        migrations.AlterField(
            model_name="submissionrevision",
            name="review_note",
            field=models.TextField(blank=True, max_length=3000, verbose_name="moderation note"),
        ),
        migrations.AlterField(
            model_name="submissionrevision",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="moderated at"),
        ),
        migrations.AlterField(
            model_name="submissionrevision",
            name="reviewed_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_revisions", to=settings.AUTH_USER_MODEL, verbose_name="moderated by"),
        ),
    ]
