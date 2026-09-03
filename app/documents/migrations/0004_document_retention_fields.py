from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0003_add_edit_delete_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="shipmentdocument",
            name="deleted_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="shipmentdocument",
            name="file_deleted_at",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddIndex(
            model_name="shipmentdocument",
            index=models.Index(fields=["is_deleted", "deleted_at"], name="idx_docs_deleted_at"),
        ),
    ]
