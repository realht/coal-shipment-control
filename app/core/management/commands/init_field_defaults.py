from django.core.management.base import BaseCommand
from core.models import FieldSettings
from core.field_config import invalidate_entity_config

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


class Command(BaseCommand):
    help = "Проставить allow_filter/allow_sort/filter_type по умолчанию из исторических whitelist"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Перезаписать уже выставленные значения",
        )

    def handle(self, *args, **options):
        force = options["force"]
        updated = 0

        for entity in ("auto_shipment", "rail_shipment"):
            filter_map = _ENTITY_FILTER_DEFAULTS[entity]
            sort_fields = _ENTITY_SORT_FIELDS[entity]

            for fs in FieldSettings.objects.filter(entity=entity):
                changed = False
                ftype = filter_map.get(fs.field_name, "none")

                if ftype != "none" and (force or not fs.allow_filter):
                    fs.allow_filter = True
                    fs.filter_type = ftype
                    changed = True

                if fs.field_name in sort_fields and (force or not fs.allow_sort):
                    fs.allow_sort = True
                    changed = True

                if changed:
                    fs.save(update_fields=["allow_filter", "allow_sort", "filter_type"])
                    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"init_field_defaults: обновлено {updated} записей"
        ))
        for entity in ("auto_shipment", "rail_shipment"):
            invalidate_entity_config(entity)
