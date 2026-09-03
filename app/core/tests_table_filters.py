# app/core/tests_table_filters.py
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.http import QueryDict

from core.table_filters import _coerce_value_for_field, apply_column_filters
from shipments_auto.models import AutoShipment


class TestCoerceValueForField:
    def test_decimal_valid(self):
        field = AutoShipment._meta.get_field("quantity")
        assert _coerce_value_for_field(field, "100.5") == Decimal("100.5")

    def test_decimal_invalid(self):
        field = AutoShipment._meta.get_field("quantity")
        assert _coerce_value_for_field(field, "abc") is None

    def test_decimal_empty(self):
        field = AutoShipment._meta.get_field("quantity")
        assert _coerce_value_for_field(field, "") is None

    def test_integer_valid(self):
        field = AutoShipment._meta.get_field("source_day_number")
        assert _coerce_value_for_field(field, "5") == 5

    def test_integer_invalid(self):
        field = AutoShipment._meta.get_field("source_day_number")
        assert _coerce_value_for_field(field, "abc") is None

    def test_integer_empty(self):
        field = AutoShipment._meta.get_field("source_day_number")
        assert _coerce_value_for_field(field, "") is None

    def test_date_valid(self):
        field = AutoShipment._meta.get_field("shipment_date")
        assert _coerce_value_for_field(field, "2026-01-15") == date(2026, 1, 15)

    def test_date_invalid(self):
        field = AutoShipment._meta.get_field("shipment_date")
        assert _coerce_value_for_field(field, "notadate") is None

    def test_date_empty(self):
        field = AutoShipment._meta.get_field("shipment_date")
        assert _coerce_value_for_field(field, "") is None

    def test_char_passthrough(self):
        field = AutoShipment._meta.get_field("coal_grade")
        assert _coerce_value_for_field(field, "ДГ") == "ДГ"

    def test_date_calendar_invalid_returns_none(self):
        """2025-02-30 — valid format, non-existent date → None, not ValueError."""
        field = AutoShipment._meta.get_field("shipment_date")
        with patch("core.table_filters.parse_date", side_effect=ValueError("day out of range")):
            assert _coerce_value_for_field(field, "2025-02-30") is None

@pytest.mark.django_db
class TestApplyColumnFiltersValueCoercion:
    def test_decimal_invalid_value_no_filter_applied(self):
        AutoShipment.objects.create(
            shipment_date="2026-01-01", customer_object="Объект", coal_grade="ДГ", quantity="100.5",
        )
        params = QueryDict("f_quantity=abc")
        qs = apply_column_filters(
            AutoShipment.objects.all(), params, {"quantity": "value"}, AutoShipment
        )
        assert qs.count() == 1  # filter not applied, all 1 record returned

    def test_decimal_valid_value_filters_correctly(self):
        s = AutoShipment.objects.create(
            shipment_date="2026-01-01", customer_object="Объект1", coal_grade="ДГ", quantity="100.5",
        )
        AutoShipment.objects.create(
            shipment_date="2026-01-02", customer_object="Объект2", coal_grade="Т", quantity="200.0",
        )
        params = QueryDict("f_quantity=100.5")
        qs = apply_column_filters(
            AutoShipment.objects.all(), params, {"quantity": "value"}, AutoShipment
        )
        assert qs.count() == 1
        assert qs.first().pk == s.pk

    def test_integer_invalid_value_no_filter_applied(self):
        AutoShipment.objects.create(
            shipment_date="2026-01-01", customer_object="Объект", coal_grade="ДГ", quantity="10",
            source_day_number=5,
        )
        params = QueryDict("f_source_day_number=abc")
        qs = apply_column_filters(
            AutoShipment.objects.all(), params, {"source_day_number": "value"}, AutoShipment
        )
        assert qs.count() == 1  # filter not applied, all 1 record returned

    def test_date_invalid_value_no_filter_applied(self):
        AutoShipment.objects.create(
            shipment_date="2026-01-01", customer_object="Объект", coal_grade="ДГ", quantity="10",
        )
        params = QueryDict("f_shipment_date=notadate")
        qs = apply_column_filters(
            AutoShipment.objects.all(), params, {"shipment_date": "value"}, AutoShipment
        )
        assert qs.count() == 1  # filter not applied, all 1 record returned

    def test_char_field_unchanged(self):
        AutoShipment.objects.create(
            shipment_date="2026-01-01", customer_object="Объект1", coal_grade="ДГ", quantity="10",
        )
        AutoShipment.objects.create(
            shipment_date="2026-01-02", customer_object="Объект2", coal_grade="Т", quantity="20",
        )
        params = QueryDict("f_coal_grade=ДГ")
        qs = apply_column_filters(
            AutoShipment.objects.all(), params, {"coal_grade": "value"}, AutoShipment
        )
        assert qs.count() == 1
        assert qs.first().coal_grade == "ДГ"

    def test_multiple_values_partial_invalid_filters_valid_only(self):
        s = AutoShipment.objects.create(
            shipment_date="2026-01-01", customer_object="Объект1", coal_grade="ДГ", quantity="100.5",
        )
        AutoShipment.objects.create(
            shipment_date="2026-01-02", customer_object="Объект2", coal_grade="Т", quantity="200.0",
        )
        params = QueryDict("f_quantity=100.5&f_quantity=abc")
        qs = apply_column_filters(
            AutoShipment.objects.all(), params, {"quantity": "value"}, AutoShipment
        )
        assert qs.count() == 1
        assert qs.first().pk == s.pk

    def test_range_filter_calendar_invalid_date_no_crash(self):
        """apply_column_filters does not crash and does not filter on calendar-invalid date in range."""
        AutoShipment.objects.create(
            shipment_date="2026-01-01", customer_object="Объект", coal_grade="ДГ", quantity="10",
        )
        params = QueryDict("f_shipment_date_from=2025-02-30")
        with patch("core.table_filters.parse_date", side_effect=ValueError("day out of range")):
            qs = apply_column_filters(
                AutoShipment.objects.all(), params, {"shipment_date": "date"}, AutoShipment
            )
        assert qs.count() == 1  # filter not applied, record returned
