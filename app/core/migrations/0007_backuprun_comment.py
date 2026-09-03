from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_system_operations"),
    ]

    operations = [
        migrations.AddField(
            model_name="backuprun",
            name="comment",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
