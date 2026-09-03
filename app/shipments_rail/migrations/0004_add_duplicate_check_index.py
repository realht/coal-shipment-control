from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shipments_rail", "0003_add_composite_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="railshipment",
            index=models.Index(
                fields=["is_deleted", "departure_date", "wagon_number", "receiver", "volume"],
                name="idx_rail_dup_check",
            ),
        ),
    ]
