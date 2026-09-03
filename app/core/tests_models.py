import pytest
from decimal import Decimal
from django.utils import timezone


@pytest.mark.django_db
class TestSoftDelete:
    def test_soft_delete_hides_from_default_manager(self):
        from shipments_auto.models import AutoShipment
        obj = AutoShipment.objects.create(
            shipment_date="2026-01-15",
            customer_object="Объект А",
            coal_grade="Д",
            quantity=Decimal("100.000"),
        )
        obj.delete()
        assert AutoShipment.objects.filter(pk=obj.pk).count() == 0

    def test_soft_delete_visible_in_all_objects(self):
        from shipments_auto.models import AutoShipment
        obj = AutoShipment.objects.create(
            shipment_date="2026-01-15",
            customer_object="Объект А",
            coal_grade="Д",
            quantity=Decimal("100.000"),
        )
        obj.delete()
        assert AutoShipment.all_objects.filter(pk=obj.pk).exists()
        assert AutoShipment.all_objects.get(pk=obj.pk).is_deleted is True

    def test_soft_delete_sets_is_deleted_flag(self):
        from shipments_auto.models import AutoShipment
        obj = AutoShipment.objects.create(
            shipment_date="2026-01-15",
            customer_object="Объект Б",
            coal_grade="Г",
            quantity=Decimal("50.500"),
        )
        obj.delete()
        obj.refresh_from_db()
        assert obj.is_deleted is True


@pytest.mark.django_db
class TestAutoShipment:
    def test_decimal_quantity_persisted(self):
        from shipments_auto.models import AutoShipment
        obj = AutoShipment.objects.create(
            shipment_date="2026-03-10",
            customer_object="Завод",
            coal_grade="ДГ",
            quantity=Decimal("1234.567"),
        )
        obj.refresh_from_db()
        assert obj.quantity == Decimal("1234.567")

    def test_str_representation(self):
        from shipments_auto.models import AutoShipment
        obj = AutoShipment(
            shipment_date="2026-03-10",
            customer_object="Завод",
            coal_grade="ДГ",
            quantity=Decimal("100.000"),
        )
        result = str(obj)
        assert "Завод" in result
        assert "ДГ" in result

    def test_optional_fields_default_empty(self):
        from shipments_auto.models import AutoShipment
        obj = AutoShipment.objects.create(
            shipment_date="2026-03-10",
            customer_object="Объект",
            coal_grade="Д",
            quantity=Decimal("10.000"),
        )
        assert obj.ttn_number == ""
        assert obj.carrier == ""
        assert obj.comment == ""

    def test_timestamps_set_on_create(self):
        from shipments_auto.models import AutoShipment
        obj = AutoShipment.objects.create(
            shipment_date="2026-03-10",
            customer_object="Объект",
            coal_grade="Д",
            quantity=Decimal("10.000"),
        )
        assert obj.created_at is not None
        assert obj.updated_at is not None


@pytest.mark.django_db
class TestRailShipment:
    def test_decimal_volume_persisted(self):
        from shipments_rail.models import RailShipment
        obj = RailShipment.objects.create(
            departure_date="2026-04-01",
            wagon_number="12345678",
            cargo="Уголь Д",
            receiver="ООО Получатель",
            volume=Decimal("4567.890"),
        )
        obj.refresh_from_db()
        assert obj.volume == Decimal("4567.890")

    def test_str_representation(self):
        from shipments_rail.models import RailShipment
        obj = RailShipment(
            departure_date="2026-04-01",
            wagon_number="12345678",
            cargo="Уголь",
            receiver="ООО Получатель",
            volume=Decimal("100.000"),
        )
        result = str(obj)
        assert "12345678" in result
        assert "ООО Получатель" in result

    def test_soft_delete_rail(self):
        from shipments_rail.models import RailShipment
        obj = RailShipment.objects.create(
            departure_date="2026-04-01",
            wagon_number="99999999",
            cargo="Уголь",
            receiver="Получатель",
            volume=Decimal("100.000"),
        )
        obj.delete()
        assert RailShipment.objects.filter(pk=obj.pk).count() == 0
        assert RailShipment.all_objects.filter(pk=obj.pk).exists()


@pytest.mark.django_db
class TestShipmentDocument:
    def test_document_str(self):
        from documents.models import ShipmentDocument
        doc = ShipmentDocument(
            shipment_type="auto",
            shipment_id=1,
            document_type="ttn",
            original_file_name="ttn_001.pdf",
            stored_file_name="uuid-abc.pdf",
            file_path="/app/uploads/auto/2026/01/shipment_1/uuid-abc.pdf",
        )
        assert "ТТН" in str(doc)
        assert "ttn_001.pdf" in str(doc)


@pytest.mark.django_db
class TestAuditLog:
    def test_audit_log_str(self):
        from audit.models import AuditLog
        log = AuditLog(
            entity_type="auto_shipment",
            entity_id=1,
            action="create",
        )
        result = str(log)
        assert "auto_shipment" in result
        assert "create" in result


@pytest.mark.django_db
class TestCustomUser:
    def test_user_str_with_full_name(self, django_user_model):
        user = django_user_model.objects.create_user(
            username="ivanov",
            password="pass",
            first_name="Иван",
            last_name="Иванов",
        )
        assert str(user) == "Иван Иванов"

    def test_user_str_without_full_name(self, django_user_model):
        user = django_user_model.objects.create_user(
            username="noname",
            password="pass",
        )
        assert str(user) == "noname"
