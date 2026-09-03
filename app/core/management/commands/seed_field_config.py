from pathlib import Path
import yaml
from django.core.management.base import BaseCommand
from core.models import FieldSettings
from core.field_config import SYSTEM_FIELDS, FIELD_LABELS, CATALOG_FIELDS, invalidate_entity_config

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "field_config.yml"


class Command(BaseCommand):
    help = "Заполнить FieldSettings из field_config.yml (seed при первом деплое)"

    def handle(self, *args, **options):
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        created_total = 0
        updated_total = 0

        for entity, fields in config.items():
            system_fields = SYSTEM_FIELDS.get(entity, set())
            catalog_fields = CATALOG_FIELDS.get(entity, set())
            labels = FIELD_LABELS.get(entity, {})

            for idx, (field_name, attrs) in enumerate(fields.items()):
                is_system = field_name in system_fields
                use_catalog = field_name in catalog_fields
                label = labels.get(field_name, "")

                obj, created = FieldSettings.objects.get_or_create(
                    entity=entity,
                    field_name=field_name,
                    defaults={
                        "label": label,
                        "visible": attrs.get("visible", True),
                        "required": attrs.get("required", False),
                        "section": attrs.get("section", "main"),
                        "is_system": is_system,
                        "show_in_list": attrs.get("visible", True),
                        "sort_order": idx,
                        "use_catalog": use_catalog,
                    },
                )
                if created:
                    created_total += 1
                else:
                    changed = False
                    if obj.is_system != is_system:
                        obj.is_system = is_system
                        changed = True
                    if not obj.label and label:
                        obj.label = label
                        changed = True
                    if obj.sort_order == 0 and idx != 0:
                        obj.sort_order = idx
                        changed = True
                    if obj.use_catalog != use_catalog and not obj.use_catalog:
                        obj.use_catalog = use_catalog
                        changed = True
                    if changed:
                        obj.save()
                        updated_total += 1

        self.stdout.write(self.style.SUCCESS(
            f"seed_field_config: создано {created_total}, обновлено {updated_total}"
        ))
        for entity in config:
            invalidate_entity_config(entity)
