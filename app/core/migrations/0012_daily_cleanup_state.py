from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_scheduler_heartbeat"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemstate",
            name="daily_cleanup_last_run_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="systemstate",
            name="daily_cleanup_last_result",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
