import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('submissions', '0006_retire_editors_and_invitations'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationQueueLock',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('version', models.PositiveBigIntegerField(default=0)),
            ],
        ),
        migrations.AddField(
            model_name='submissionrevision',
            name='notification_keys',
            field=models.JSONField(default=list, editable=False),
        ),
        migrations.CreateModel(
            name='NotificationDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('confirmation', 'Submission confirmation'), ('changes', 'Changes requested')], max_length=12)),
                ('recipient_digest', models.CharField(max_length=64)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('sending', 'Sending'), ('sent', 'Sent'), ('failed', 'Failed'), ('suppressed', 'Suppressed'), ('cancelled', 'Cancelled'), ('expired', 'Expired')], default='queued', max_length=12)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ('attempted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('account', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notification_deliveries', to=settings.AUTH_USER_MODEL)),
                ('revision', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notification_deliveries', to='submissions.submissionrevision')),
            ],
            options={
                'indexes': [models.Index(fields=['status', 'created_at'], name='notification_pending'), models.Index(fields=['recipient_digest', 'created_at'], name='notification_recipient')],
                'constraints': [models.UniqueConstraint(fields=('revision', 'kind', 'recipient_digest'), name='unique_revision_notice_recipient')],
            },
        ),
    ]
