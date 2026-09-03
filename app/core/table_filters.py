from decimal import Decimal, InvalidOperation

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.utils.dateparse import parse_date, parse_datetime


RANGE_FILTER_TYPES = {"date", "number"}


def parse_top_level_date_bound(raw_value):
    """Parse top-level date_from/date_to GET values; return None for invalid input."""
    value = (raw_value or "").strip()
    if not value:
        return None
    try:
        return parse_date(value)
    except (TypeError, ValueError):
        return None


def _coerce_value_for_field(field, raw_value):
    """Coerce raw string value to field's native type, or return None if invalid."""
    value = (raw_value or "").strip()
    if not value:
        return None

    if isinstance(field, models.DateTimeField):
        try:
            return parse_datetime(value) or parse_date(value)
        except (TypeError, ValueError):
            return None
    elif isinstance(field, models.DateField):
        try:
            return parse_date(value)
        except (TypeError, ValueError):
            return None
    elif isinstance(field, (models.IntegerField, models.AutoField, models.BigAutoField)):
        try:
            return int(value)
        except (ValueError, InvalidOperation):
            return None
    elif isinstance(field, models.DecimalField):
        try:
            return Decimal(value)
        except (ValueError, InvalidOperation):
            return None
    else:
        # CharField and other text fields: passthrough
        return raw_value


def _model_field(model, field_name):
    if "__" in field_name:
        return None
    try:
        field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return None
    return field if isinstance(field, models.Field) else None


def _coerce_range_value(field, filter_type, raw_value):
    value = (raw_value or "").strip()
    if not value:
        return None

    if filter_type == "date":
        try:
            if isinstance(field, models.DateTimeField):
                return parse_datetime(value) or parse_date(value)
            return parse_date(value)
        except (TypeError, ValueError):
            return None

    if filter_type == "number":
        try:
            if isinstance(field, (models.IntegerField, models.AutoField, models.BigAutoField)):
                return int(value)
            return Decimal(value)
        except (InvalidOperation, ValueError):
            return None

    return None


def apply_column_filters(queryset, params, filter_config, model):
    for field_name, filter_type in filter_config.items():
        field = _model_field(model, field_name)
        if field is None:
            continue

        if filter_type == "value":
            raw_values = [v for v in params.getlist(f"f_{field_name}") if v != ""]
            if raw_values:
                coerced_values = [_coerce_value_for_field(field, v) for v in raw_values]
                values = [v for v in coerced_values if v is not None]
                if values:
                    queryset = queryset.filter(**{f"{field_name}__in": values})
        elif filter_type in RANGE_FILTER_TYPES:
            from_value = _coerce_range_value(field, filter_type, params.get(f"f_{field_name}_from", ""))
            to_value = _coerce_range_value(field, filter_type, params.get(f"f_{field_name}_to", ""))
            if from_value is not None:
                queryset = queryset.filter(**{f"{field_name}__gte": from_value})
            if to_value is not None:
                queryset = queryset.filter(**{f"{field_name}__lte": to_value})
        elif filter_type == "text":
            value = params.get(f"f_{field_name}", "").strip()
            if value:
                queryset = queryset.filter(**{f"{field_name}__icontains": value})

    return queryset


def active_column_filters(params, filter_config):
    value_fields = {field for field, filter_type in filter_config.items() if filter_type == "value"}
    range_fields = {field for field, filter_type in filter_config.items() if filter_type in RANGE_FILTER_TYPES}
    text_fields = {field for field, filter_type in filter_config.items() if filter_type == "text"}
    active_values = {
        field: [value for value in params.getlist(f"f_{field}") if value != ""]
        for field in value_fields
    }

    return {
        "active_filters": {
            field: values
            for field, values in active_values.items()
            if values
        },
        "active_range_filters": {
            field: {
                "from": params.get(f"f_{field}_from", ""),
                "to": params.get(f"f_{field}_to", ""),
            }
            for field in range_fields
            if params.get(f"f_{field}_from") or params.get(f"f_{field}_to")
        },
        "active_text_filters": {
            field: params.get(f"f_{field}", "").strip()
            for field in text_fields
            if params.get(f"f_{field}", "").strip()
        },
    }
