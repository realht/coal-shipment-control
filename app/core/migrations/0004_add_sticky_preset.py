from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_add_filter_sort_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="fieldsettings",
            name="sticky_col",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="fieldsettings",
            name="preset_membership",
            field=models.CharField(max_length=200, blank=True, default=""),
        ),
    ]
