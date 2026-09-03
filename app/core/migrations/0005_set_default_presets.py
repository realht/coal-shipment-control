from django.db import migrations


AUTO_STICKY = {"shipment_date", "customer_object", "vehicle_number"}
RAIL_STICKY = {"departure_date", "wagon_number", "receiver"}

AUTO_PRESETS = {
    "shipment_date":    "operative,documents,logistics",
    "customer_object":  "operative,documents,logistics",
    "vehicle_number":   "operative,logistics",
    "driver_name":      "logistics",
    "ttn_number":       "documents",
    "coal_grade":       "operative,documents,logistics",
    "quantity":         "operative,documents,logistics",
    "carrier":          "logistics",
    "upd_number":       "documents",
}

RAIL_PRESETS = {
    "departure_date":       "operative,route,documents",
    "wagon_number":         "operative,route,documents",
    "origin_station":       "route",
    "destination_station":  "route",
    "receiver":             "operative,route,documents",
    "volume":               "operative,route,documents",
    "cargo":                "operative,documents",
    "document_number":      "documents",
}


def set_defaults(apps, schema_editor):
    FieldSettings = apps.get_model("core", "FieldSettings")

    for fs in FieldSettings.objects.filter(entity="auto_shipment"):
        changed = False
        if fs.field_name in AUTO_STICKY and not fs.sticky_col:
            fs.sticky_col = True
            changed = True
        if not fs.preset_membership and fs.field_name in AUTO_PRESETS:
            fs.preset_membership = AUTO_PRESETS[fs.field_name]
            changed = True
        if changed:
            fs.save(update_fields=["sticky_col", "preset_membership"])

    for fs in FieldSettings.objects.filter(entity="rail_shipment"):
        changed = False
        if fs.field_name in RAIL_STICKY and not fs.sticky_col:
            fs.sticky_col = True
            changed = True
        if not fs.preset_membership and fs.field_name in RAIL_PRESETS:
            fs.preset_membership = RAIL_PRESETS[fs.field_name]
            changed = True
        if changed:
            fs.save(update_fields=["sticky_col", "preset_membership"])


def unset_defaults(apps, schema_editor):
    FieldSettings = apps.get_model("core", "FieldSettings")
    FieldSettings.objects.filter(entity__in=["auto_shipment", "rail_shipment"]).update(
        sticky_col=False, preset_membership=""
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_add_sticky_preset"),
    ]

    operations = [
        migrations.RunPython(set_defaults, unset_defaults),
    ]
