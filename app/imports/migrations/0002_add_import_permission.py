from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("imports", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="importlog",
            options={
                "db_table": "import_logs",
                "ordering": ["-created_at"],
                "permissions": [
                    ("import_shipments", "Может импортировать отгрузки из Excel"),
                ],
                "verbose_name": "Журнал импорта",
                "verbose_name_plural": "Журнал импортов",
            },
        ),
    ]
