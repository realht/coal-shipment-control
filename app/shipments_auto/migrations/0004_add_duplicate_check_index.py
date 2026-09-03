from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("shipments_auto", "0003_add_composite_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="autoshipment",
            index=models.Index(
                fields=["is_deleted", "shipment_date", "customer_object", "coal_grade", "quantity"],
                name="idx_auto_dup_check",
            ),
        ),
    ]
