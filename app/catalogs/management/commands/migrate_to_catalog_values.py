from django.core.management.base import BaseCommand
from catalogs.models import AutoBase, AutoCoalGrade, RailCoalGrade, CatalogValue


class Command(BaseCommand):
    help = "Перенести AutoBase, AutoCoalGrade, RailCoalGrade → CatalogValue"

    def handle(self, *args, **options):
        added = 0

        for obj in AutoCoalGrade.objects.all():
            _, created = CatalogValue.objects.get_or_create(
                catalog_type=CatalogValue.TYPE_AUTO_GRADE,
                name=obj.name,
                defaults={"is_active": obj.is_active},
            )
            if created:
                added += 1

        for obj in AutoBase.objects.all():
            _, created = CatalogValue.objects.get_or_create(
                catalog_type=CatalogValue.TYPE_AUTO_BASE,
                name=obj.name,
                defaults={"is_active": obj.is_active},
            )
            if created:
                added += 1

        for obj in RailCoalGrade.objects.all():
            _, created = CatalogValue.objects.get_or_create(
                catalog_type=CatalogValue.TYPE_RAIL_GRADE,
                name=obj.name,
                defaults={"is_active": obj.is_active},
            )
            if created:
                added += 1

        self.stdout.write(self.style.SUCCESS(
            f"migrate_to_catalog_values: перенесено {added} записей в CatalogValue"
        ))
