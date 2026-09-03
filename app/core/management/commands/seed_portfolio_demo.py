"""Seed a clearly synthetic dataset for the public portfolio showcase."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import BaseCommand, call_command
from django.utils import timezone

from accounts.models import User
from audit.models import AuditLog
from catalogs.models import CatalogValue
from documents.models import ShipmentDocument
from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment


DEMO_USERNAME = "portfolio_admin"
DEMO_PASSWORD = "portfolio-demo"


class Command(BaseCommand):
    help = "Создать синтетические данные и demo-пользователя для portfolio showcase."

    def handle(self, *args, **options):
        call_command("seed_groups")
        call_command("seed_field_config")

        admin_group = Group.objects.get(name="admin")
        user, _ = User.objects.update_or_create(
            username=DEMO_USERNAME,
            defaults={
                "first_name": "Портфолио",
                "last_name": "Демо",
                "email": "portfolio@example.test",
                "is_staff": False,
                "is_superuser": False,
                "is_active": True,
            },
        )
        user.set_password(DEMO_PASSWORD)
        user.save(update_fields=["password", "first_name", "last_name", "email", "is_staff", "is_superuser", "is_active"])
        user.groups.set([admin_group])

        for catalog_type, name in (
            ("auto_shipment__coal_grade", "ДПК"),
            ("auto_shipment__coal_grade", "КО"),
            ("rail_shipment__cargo", "Уголь энергетический"),
        ):
            CatalogValue.objects.get_or_create(catalog_type=catalog_type, name=name)

        today = timezone.localdate()
        auto_rows = (
            {
                "shipment_date": today,
                "customer_object": "Демо-объект «Север»",
                "vehicle_number": "ДЕМО-101",
                "driver_name": "Алексей Демо",
                "ttn_number": "DEMO-AUTO-001",
                "coal_grade": "ДПК",
                "quantity": Decimal("24.500"),
                "carrier": "Демо-логистика",
                "comment": "Синтетическая запись для публичного showcase.",
            },
            {
                "shipment_date": today - timedelta(days=3),
                "customer_object": "Демо-объект «Центр»",
                "vehicle_number": "ДЕМО-102",
                "driver_name": "Мария Демо",
                "ttn_number": "DEMO-AUTO-002",
                "coal_grade": "КО",
                "quantity": Decimal("18.000"),
                "carrier": "Демо-логистика",
                "comment": "Синтетическая запись для фильтрации и экспорта.",
            },
            {
                "shipment_date": today - timedelta(days=11),
                "customer_object": "Демо-объект «Восток»",
                "vehicle_number": "ДЕМО-103",
                "driver_name": "Иван Демо",
                "ttn_number": "DEMO-AUTO-003",
                "coal_grade": "ДПК",
                "quantity": Decimal("31.250"),
                "carrier": "Демо-транс",
                "comment": "Синтетическая запись без документа.",
            },
        )
        auto_shipments = []
        for values in auto_rows:
            values = {**values, "created_by": user, "updated_by": user}
            shipment, _ = AutoShipment.objects.update_or_create(
                ttn_number=values["ttn_number"], defaults=values
            )
            auto_shipments.append(shipment)

        rail_rows = (
            {
                "departure_date": today - timedelta(days=1),
                "wagon_number": "ДЕМО-ВАГОН-001",
                "document_number": "DEMO-RAIL-001",
                "cargo": "Уголь энергетический",
                "origin_region": "Демо-регион",
                "origin_station": "Демо-Отправление",
                "destination_region": "Демо-регион",
                "destination_station": "Демо-Прибытие",
                "receiver": "Демо-получатель",
                "volume": Decimal("69.000"),
                "comment": "Синтетическая ЖД-отгрузка.",
            },
            {
                "departure_date": today - timedelta(days=8),
                "wagon_number": "ДЕМО-ВАГОН-002",
                "document_number": "DEMO-RAIL-002",
                "cargo": "Уголь энергетический",
                "origin_region": "Демо-регион",
                "origin_station": "Демо-Отправление",
                "destination_region": "Демо-регион",
                "destination_station": "Демо-Прибытие",
                "receiver": "Демо-получатель",
                "volume": Decimal("71.500"),
                "comment": "Синтетическая запись для dashboard.",
            },
        )
        rail_shipments = []
        for values in rail_rows:
            values = {**values, "created_by": user, "updated_by": user}
            shipment, _ = RailShipment.objects.update_or_create(
                document_number=values["document_number"], defaults=values
            )
            rail_shipments.append(shipment)

        self._seed_document(auto_shipments[0], user)
        self._seed_audit_log(auto_shipments[0], rail_shipments[0], user)

        self.stdout.write(self.style.SUCCESS(
            "Portfolio demo data is ready. "
            f"Login: {DEMO_USERNAME} / {DEMO_PASSWORD}"
        ))

    def _seed_document(self, shipment, user):
        relative_path = Path("auto") / "demo" / "demo-waybill.pdf"
        target = Path(settings.MEDIA_ROOT) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        content = b"%PDF-1.4\n% Demo document for portfolio showcase\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
        target.write_bytes(content)
        ShipmentDocument.objects.update_or_create(
            shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
            shipment_id=shipment.pk,
            original_file_name="demo-waybill.pdf",
            defaults={
                "document_type": ShipmentDocument.DOCUMENT_TYPE_TTN,
                "stored_file_name": "demo-waybill.pdf",
                "file_path": str(relative_path),
                "mime_type": "application/pdf",
                "file_size": len(content),
                "uploaded_by": user,
                "is_deleted": False,
                "deleted_at": None,
                "file_deleted_at": None,
            },
        )

    def _seed_audit_log(self, auto_shipment, rail_shipment, user):
        audit_rows = (
            (AuditLog.ENTITY_AUTO, auto_shipment.pk, AuditLog.ACTION_CREATE, {"source": "portfolio demo"}),
            (AuditLog.ENTITY_RAIL, rail_shipment.pk, AuditLog.ACTION_UPDATE, {"volume": "69.000"}),
        )
        for entity_type, entity_id, action, new_values in audit_rows:
            AuditLog.objects.get_or_create(
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                source=AuditLog.SOURCE_SCRIPT,
                defaults={"new_values": new_values, "user": user, "user_agent": "portfolio-demo"},
            )
