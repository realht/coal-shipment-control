from django.db.models import Sum
from django.utils import timezone

from documents.models import ShipmentDocument
from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment

_MONTHS_RU = [
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


def _doc_ids_qs(shipment_type):
    """Return a values queryset of shipment_id for non-deleted documents of the given type."""
    return ShipmentDocument.objects.filter(
        shipment_type=shipment_type,
        is_deleted=False,
    ).values("shipment_id")


def get_dashboard_stats(can_view_auto: bool, can_view_rail: bool) -> dict:
    if not can_view_auto and not can_view_rail:
        return {}

    today = timezone.localdate()
    month_start = today.replace(day=1)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1)
    stats: dict = {
        "month_label": f"{_MONTHS_RU[today.month].capitalize()} {today.year}",
        "can_view_auto": can_view_auto,
        "can_view_rail": can_view_rail,
    }

    if can_view_auto:
        auto_qs = AutoShipment.objects.filter(is_deleted=False)
        auto_month_qs = auto_qs.filter(
            shipment_date__gte=month_start,
            shipment_date__lt=next_month_start,
        )
        docs_auto = _doc_ids_qs(ShipmentDocument.SHIPMENT_TYPE_AUTO)
        stats["auto_total_month"] = auto_month_qs.aggregate(t=Sum("quantity"))["t"] or 0
        stats["auto_count_month"] = auto_month_qs.count()
        stats["auto_total_all"] = auto_qs.aggregate(t=Sum("quantity"))["t"] or 0
        stats["auto_no_docs"] = auto_qs.exclude(id__in=docs_auto).count()
        # auto-only: ЖД не использует ТТН — учёт идёт по номерам вагонов, поэтому rail_ttn_no_file отсутствует намеренно
        stats["auto_ttn_no_file"] = auto_qs.exclude(ttn_number="").exclude(id__in=docs_auto).count()
        stats["auto_by_grade"] = list(
            auto_qs.values("coal_grade").annotate(total=Sum("quantity")).order_by("-total")[:8]
        )

    if can_view_rail:
        rail_qs = RailShipment.objects.filter(is_deleted=False)
        rail_month_qs = rail_qs.filter(
            departure_date__gte=month_start,
            departure_date__lt=next_month_start,
        )
        docs_rail = _doc_ids_qs(ShipmentDocument.SHIPMENT_TYPE_RAIL)
        stats["rail_total_month"] = rail_month_qs.aggregate(t=Sum("volume"))["t"] or 0
        stats["rail_count_month"] = rail_month_qs.count()
        stats["rail_total_all"] = rail_qs.aggregate(t=Sum("volume"))["t"] or 0
        stats["rail_no_docs"] = rail_qs.exclude(id__in=docs_rail).count()
        stats["rail_by_grade"] = list(
            rail_qs.values("cargo").annotate(total=Sum("volume")).order_by("-total")[:8]
        )

    return stats
