from django.core.management.base import BaseCommand
from catalogs.models import CatalogValue

_KEY_MAP = {
    "auto_coal_grade": "auto_shipment__coal_grade",
    "auto_base": "auto_shipment__base_code",
    "rail_coal_grade": "rail_shipment__cargo",
}


class Command(BaseCommand):
    help = "Переименовать старые catalog_type ключи в формат entity__field_name"

    def handle(self, *args, **options):
        updated = 0
        for old_key, new_key in _KEY_MAP.items():
            count = CatalogValue.objects.filter(catalog_type=old_key).count()
            if count:
                CatalogValue.objects.filter(catalog_type=old_key).update(catalog_type=new_key)
                updated += count
                self.stdout.write(f"  {old_key} → {new_key}: {count} записей")
        self.stdout.write(self.style.SUCCESS(f"migrate_catalog_type_keys: обновлено {updated} записей"))
