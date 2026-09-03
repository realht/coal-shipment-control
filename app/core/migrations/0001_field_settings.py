from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="FieldSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entity", models.CharField(max_length=50)),
                ("field_name", models.CharField(max_length=100)),
                ("visible", models.BooleanField(default=True)),
                ("required", models.BooleanField(default=False)),
                ("section", models.CharField(default="main", max_length=20)),
                ("is_system", models.BooleanField(default=False)),
                ("show_in_list", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "field_settings",
                "ordering": ["entity", "field_name"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="fieldsettings",
            unique_together={("entity", "field_name")},
        ),
    ]
