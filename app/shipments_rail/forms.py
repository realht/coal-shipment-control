from django import forms
from core.field_config import FIELD_LABELS
from core.forms import CatalogHybridFormMixin
from .models import RailShipment

_ALL_FIELDS = [
    "departure_date",
    "wagon_number",
    "document_number",
    "cargo",
    "destination_station",
    "receiver",
    "volume",
    "comment",
    "origin_region",
    "origin_station",
    "sender",
    "destination_region",
]

_LABELS = FIELD_LABELS["rail_shipment"]


class RailShipmentForm(CatalogHybridFormMixin, forms.ModelForm):
    entity = "rail_shipment"
    all_fields = _ALL_FIELDS
    labels = _LABELS
    date_field = "departure_date"

    class Meta:
        model = RailShipment
        fields = _ALL_FIELDS
        widgets = {
            "departure_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "comment": forms.Textarea(attrs={"rows": 3}),
        }
        labels = _LABELS
