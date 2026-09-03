from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_uploads_size_cache"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemstate",
            name="scheduler_heartbeat_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
    ]
