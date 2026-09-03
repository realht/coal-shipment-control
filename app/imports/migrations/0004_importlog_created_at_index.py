from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("imports", "0003_importlog_skipped_rows_importlog_updated_rows_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="importlog",
            index=models.Index(fields=["created_at"], name="idx_import_log_created"),
        ),
    ]
