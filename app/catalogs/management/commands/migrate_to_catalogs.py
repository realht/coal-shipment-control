from django.core.management.base import BaseCommand
from catalogs.models import AutoBase, AutoCoalGrade, RailCoalGrade
from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment


class Command(BaseCommand):
    help = "Добавить в справочники все уникальные значения из существующих отгрузок"

    def handle(self, *args, **options):
        added = 0

        auto_grades = (
            AutoShipment.objects.filter(is_deleted=False)
            .exclude(coal_grade="")
            .values_list("coal_grade", flat=True)
            .distinct()
        )
        for name in auto_grades:
            _, created = AutoCoalGrade.objects.get_or_create(name=name)
            if created:
                added += 1
                self.stdout.write(f"  AutoCoalGrade: {name}")

        auto_bases = (
            AutoShipment.objects.filter(is_deleted=False)
            .exclude(base_code="")
            .values_list("base_code", flat=True)
            .distinct()
        )
        for name in auto_bases:
            _, created = AutoBase.objects.get_or_create(name=name)
            if created:
                added += 1
                self.stdout.write(f"  AutoBase: {name}")

        rail_grades = (
            RailShipment.objects.filter(is_deleted=False)
            .exclude(cargo="")
            .values_list("cargo", flat=True)
            .distinct()
        )
        for name in rail_grades:
            _, created = RailCoalGrade.objects.get_or_create(name=name)
            if created:
                added += 1
                self.stdout.write(f"  RailCoalGrade: {name}")

        self.stdout.write(self.style.SUCCESS(
            f"Готово. Добавлено новых записей в справочники: {added}"
        ))
