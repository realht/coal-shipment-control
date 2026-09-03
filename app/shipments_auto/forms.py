from django import forms
from core.field_config import FIELD_LABELS
from core.forms import CatalogHybridFormMixin
from .models import AutoShipment

_ALL_FIELDS = [
    "shipment_date",
    "customer_object",
    "vehicle_number",
    "driver_name",
    "ttn_number",
    "coal_grade",
    "quantity",
    "carrier",
    "comment",
    "sub_object",
    "base_code",
    "upd_number",
    "balance_note",
    "source_month_text",
    "source_day_number",
]

_LABELS = FIELD_LABELS["auto_shipment"]


class AutoShipmentForm(CatalogHybridFormMixin, forms.ModelForm):
    entity = "auto_shipment"
    all_fields = _ALL_FIELDS
    labels = _LABELS
    date_field = "shipment_date"

    class Meta:
        model = AutoShipment
        fields = _ALL_FIELDS
        widgets = {
            "shipment_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "comment": forms.Textarea(attrs={"rows": 3}),
        }
        labels = _LABELS
