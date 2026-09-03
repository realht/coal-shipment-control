import pytest
from pathlib import Path
import yaml
from core.field_config import FIELD_LABELS, CATALOG_FIELDS, SYSTEM_FIELDS as FC_SYSTEM_FIELDS

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "field_config.yml"

SYSTEM_FIELDS = FC_SYSTEM_FIELDS

_CATALOG_FIELDS = CATALOG_FIELDS


_AUTO_FILTER_DEFAULTS = {
    "shipment_date": "date",
    "customer_object": "value",
    "vehicle_number": "value",
    "driver_name": "value",
    "coal_grade": "value",
    "carrier": "value",
    "quantity": "number",
}
_AUTO_SORT_FIELDS = {
    "shipment_date", "customer_object", "vehicle_number",
    "driver_name", "coal_grade", "quantity", "carrier",
}
_RAIL_FILTER_DEFAULTS = {
    "departure_date": "date",
    "wagon_number": "value",
    "document_number": "value",
    "cargo": "value",
    "destination_station": "value",
    "receiver": "value",
    "volume": "number",
}
_RAIL_SORT_FIELDS = {
    "departure_date", "wagon_number", "document_number",
    "cargo", "destination_station", "receiver", "volume",
}
_ENTITY_FILTER_DEFAULTS = {
    "auto_shipment": _AUTO_FILTER_DEFAULTS,
    "rail_shipment": _RAIL_FILTER_DEFAULTS,
}
_ENTITY_SORT_FIELDS = {
    "auto_shipment": _AUTO_SORT_FIELDS,
    "rail_shipment": _RAIL_SORT_FIELDS,
}

_AUTO_STICKY = {"shipment_date", "customer_object", "vehicle_number"}
_RAIL_STICKY = {"departure_date", "wagon_number", "receiver"}
_ENTITY_STICKY = {
    "auto_shipment": _AUTO_STICKY,
    "rail_shipment": _RAIL_STICKY,
}

_AUTO_PRESETS = {
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
_RAIL_PRESETS = {
    "departure_date":       "operative,route,documents",
    "wagon_number":         "operative,route,documents",
    "origin_station":       "route",
    "destination_station":  "route",
    "receiver":             "operative,route,documents",
    "volume":               "operative,route,documents",
    "cargo":                "operative,documents",
    "document_number":      "documents",
}
_ENTITY_PRESETS = {
    "auto_shipment": _AUTO_PRESETS,
    "rail_shipment": _RAIL_PRESETS,
}


@pytest.fixture(autouse=True, scope="session")
def seed_field_settings(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        from core.models import FieldSettings
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        for entity, fields in config.items():
            system = SYSTEM_FIELDS.get(entity, set())
            catalog = _CATALOG_FIELDS.get(entity, set())
            labels = FIELD_LABELS.get(entity, {})
            fmap = _ENTITY_FILTER_DEFAULTS.get(entity, {})
            sfields = _ENTITY_SORT_FIELDS.get(entity, set())
            sticky = _ENTITY_STICKY.get(entity, set())
            presets_map = _ENTITY_PRESETS.get(entity, {})
            for idx, (field_name, attrs) in enumerate(fields.items()):
                ftype = fmap.get(field_name, "none")
                obj, created = FieldSettings.objects.get_or_create(
                    entity=entity,
                    field_name=field_name,
                    defaults={
                        "label": labels.get(field_name, ""),
                        "visible": attrs.get("visible", True),
                        "required": attrs.get("required", False),
                        "section": attrs.get("section", "main"),
                        "is_system": field_name in system,
                        "show_in_list": attrs.get("visible", True),
                        "sort_order": idx,
                        "use_catalog": field_name in catalog,
                        "allow_filter": ftype != "none",
                        "allow_sort": field_name in sfields,
                        "filter_type": ftype,
                        "sticky_col": field_name in sticky,
                        "preset_membership": presets_map.get(field_name, ""),
                    },
                )
                if not created:
                    updated = False
                    if obj.allow_filter != (ftype != "none"):
                        obj.allow_filter = ftype != "none"
                        updated = True
                    if obj.allow_sort != (field_name in sfields):
                        obj.allow_sort = field_name in sfields
                        updated = True
                    if obj.filter_type != ftype:
                        obj.filter_type = ftype
                        updated = True
                    if obj.sticky_col != (field_name in sticky):
                        obj.sticky_col = field_name in sticky
                        updated = True
                    if not obj.preset_membership and field_name in presets_map:
                        obj.preset_membership = presets_map[field_name]
                        updated = True
                    if updated:
                        obj.save(update_fields=["allow_filter", "allow_sort", "filter_type", "sticky_col", "preset_membership"])
        from core.field_config import invalidate_entity_config
        invalidate_entity_config("auto_shipment")
        invalidate_entity_config("rail_shipment")
