import pytest
from django.contrib.auth import get_user_model

from core.dashboard_service import get_dashboard_stats


@pytest.mark.django_db
class TestGetDashboardStats:
    @pytest.fixture
    def user(self, django_user_model):
        return django_user_model.objects.create_user(username="svc_user", password="pass")

    def test_no_perms_returns_empty_dict(self):
        assert get_dashboard_stats(False, False) == {}

    def test_auto_only_keys_present(self, user):
        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=False)
        assert "auto_count_month" in stats
        assert "auto_total_month" in stats
        assert "auto_total_all" in stats
        assert "auto_no_docs" in stats
        assert "auto_ttn_no_file" in stats
        assert "auto_by_grade" in stats
        assert "rail_count_month" not in stats
        assert "rail_total_all" not in stats

    def test_rail_only_keys_present(self, user):
        stats = get_dashboard_stats(can_view_auto=False, can_view_rail=True)
        assert "rail_count_month" in stats
        assert "rail_total_month" in stats
        assert "rail_total_all" in stats
        assert "rail_no_docs" in stats
        assert "rail_by_grade" in stats
        assert "auto_count_month" not in stats
        assert "auto_total_all" not in stats

    def test_auto_total_month_sums_correctly(self, user):
        from shipments_auto.models import AutoShipment
        from django.utils import timezone

        today = timezone.localdate()
        AutoShipment.objects.create(
            shipment_date=today, customer_object="А", coal_grade="ДГ", quantity="300",
            created_by=user, updated_by=user,
        )
        AutoShipment.objects.create(
            shipment_date=today, customer_object="Б", coal_grade="Д", quantity="200",
            created_by=user, updated_by=user,
        )
        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=False)
        assert stats["auto_total_month"] == 500
        assert stats["auto_count_month"] == 2

    def test_auto_month_metrics_exclude_next_month_shipments(self, user, monkeypatch):
        import datetime
        from decimal import Decimal
        from shipments_auto.models import AutoShipment

        monkeypatch.setattr("core.dashboard_service.timezone.localdate", lambda: datetime.date(2026, 6, 15))
        AutoShipment.objects.create(
            shipment_date=datetime.date(2026, 6, 1), customer_object="Июнь",
            coal_grade="ДГ", quantity=Decimal("300"), created_by=user, updated_by=user,
        )
        AutoShipment.objects.create(
            shipment_date=datetime.date(2026, 7, 1), customer_object="Июль",
            coal_grade="ДГ", quantity=Decimal("200"), created_by=user, updated_by=user,
        )

        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=False)

        assert stats["auto_count_month"] == 1
        assert stats["auto_total_month"] == Decimal("300")

    def test_rail_month_metrics_exclude_next_month_shipments(self, user, monkeypatch):
        import datetime
        from decimal import Decimal
        from shipments_rail.models import RailShipment

        monkeypatch.setattr("core.dashboard_service.timezone.localdate", lambda: datetime.date(2026, 6, 15))
        RailShipment.objects.create(
            departure_date=datetime.date(2026, 6, 30), wagon_number="11111111",
            cargo="ДГ", receiver="Получатель", volume=Decimal("700"), created_by=user, updated_by=user,
        )
        RailShipment.objects.create(
            departure_date=datetime.date(2026, 7, 1), wagon_number="22222222",
            cargo="ДГ", receiver="Получатель", volume=Decimal("500"), created_by=user, updated_by=user,
        )

        stats = get_dashboard_stats(can_view_auto=False, can_view_rail=True)

        assert stats["rail_count_month"] == 1
        assert stats["rail_total_month"] == Decimal("700")

    def test_month_metrics_handle_december_rollover(self, user, monkeypatch):
        import datetime
        from decimal import Decimal
        from shipments_auto.models import AutoShipment

        monkeypatch.setattr("core.dashboard_service.timezone.localdate", lambda: datetime.date(2026, 12, 15))
        AutoShipment.objects.create(
            shipment_date=datetime.date(2026, 12, 31), customer_object="Декабрь",
            coal_grade="ДГ", quantity=Decimal("400"), created_by=user, updated_by=user,
        )
        AutoShipment.objects.create(
            shipment_date=datetime.date(2027, 1, 1), customer_object="Январь",
            coal_grade="ДГ", quantity=Decimal("600"), created_by=user, updated_by=user,
        )

        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=False)

        assert stats["auto_count_month"] == 1
        assert stats["auto_total_month"] == Decimal("400")

    def test_auto_no_docs_counts_correctly(self, user):
        from shipments_auto.models import AutoShipment
        from documents.models import ShipmentDocument
        from django.utils import timezone

        today = timezone.localdate()
        ship_no_doc = AutoShipment.objects.create(
            shipment_date=today, customer_object="БезДок", coal_grade="ДГ", quantity="100",
            created_by=user, updated_by=user,
        )
        ship_with_doc = AutoShipment.objects.create(
            shipment_date=today, customer_object="СДок", coal_grade="ДГ", quantity="100",
            created_by=user, updated_by=user,
        )
        ShipmentDocument.objects.create(
            shipment_type=ShipmentDocument.SHIPMENT_TYPE_AUTO,
            shipment_id=ship_with_doc.pk,
            document_type="ttn",
            file_path="auto/2026/01/shipment_1/doc.pdf",
            original_file_name="doc.pdf",
            stored_file_name="doc.pdf",
            uploaded_by=user,
        )
        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=False)
        assert stats["auto_no_docs"] >= 1
        assert ship_no_doc.pk is not None

    def test_auto_ttn_no_file_counts_correctly(self, user):
        from shipments_auto.models import AutoShipment
        from django.utils import timezone

        today = timezone.localdate()
        AutoShipment.objects.create(
            shipment_date=today, customer_object="ТТН", coal_grade="ДГ", quantity="50",
            ttn_number="ТТН-001",
            created_by=user, updated_by=user,
        )
        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=False)
        assert stats["auto_ttn_no_file"] >= 1

    def test_month_label_format(self):
        from django.utils import timezone
        today = timezone.localdate()
        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=False)
        label = stats["month_label"]
        assert str(today.year) in label
        assert len(label) > 4

    # --- V14-L10 regression tests ---

    def test_both_no_docs_keys_present_when_both_enabled(self, user):
        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=True)
        assert "auto_no_docs" in stats
        assert "rail_no_docs" in stats

    def test_auto_ttn_no_file_present_rail_ttn_no_file_absent(self, user):
        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=True)
        assert "auto_ttn_no_file" in stats
        assert "rail_ttn_no_file" not in stats

    def test_ttn_no_file_counts_shipment_with_ttn_and_no_doc(self, user):
        from decimal import Decimal
        from django.utils import timezone
        from shipments_auto.models import AutoShipment

        today = timezone.localdate()
        AutoShipment.objects.create(
            shipment_date=today,
            customer_object="ТТН без файла",
            coal_grade="ДГ",
            quantity=Decimal("100"),
            ttn_number="ТТН-999",
            created_by=user,
            updated_by=user,
        )
        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=False)
        assert stats["auto_ttn_no_file"] >= 1

    def test_ttn_no_file_excludes_shipment_without_ttn(self, user):
        from decimal import Decimal
        from django.utils import timezone
        from shipments_auto.models import AutoShipment

        today = timezone.localdate()
        AutoShipment.objects.create(
            shipment_date=today,
            customer_object="Без ТТН",
            coal_grade="ДГ",
            quantity=Decimal("100"),
            ttn_number="",
            created_by=user,
            updated_by=user,
        )
        stats = get_dashboard_stats(can_view_auto=True, can_view_rail=False)
        assert stats["auto_ttn_no_file"] == 0
