from django.core.management.base import BaseCommand
from catalogs.models import CatalogValue

AUTO_BASES = ["Озеры", "Тучково", "Тушкино"]

AUTO_COAL_GRADES = ["АК", "АМ", "АО", "АС", "ДГПК", "ДМС", "ДО", "ДОМ", "ДПК", "ДР", "ССПК", "Т", "ТПК"]

RAIL_COAL_GRADES = ["Антрацит", "Уголь Д", "Уголь СС", "Уголь Т"]


class Command(BaseCommand):
    help = "Заполнить справочники начальными данными"

    def handle(self, *args, **options):
        created_total = 0

        seeds = [
            (CatalogValue.TYPE_AUTO_BASE, AUTO_BASES),
            (CatalogValue.TYPE_AUTO_GRADE, AUTO_COAL_GRADES),
            (CatalogValue.TYPE_RAIL_GRADE, RAIL_COAL_GRADES),
        ]
        for catalog_type, names in seeds:
            for name in names:
                _, created = CatalogValue.objects.get_or_create(
                    catalog_type=catalog_type, name=name
                )
                if created:
                    created_total += 1

        self.stdout.write(self.style.SUCCESS(f"Справочники заполнены, добавлено записей: {created_total}"))
