from django.core.cache import cache

CACHE_TTL = 300

SYSTEM_FIELDS = {
    "auto_shipment": {"shipment_date", "coal_grade", "quantity"},
    "rail_shipment": {"departure_date", "wagon_number", "cargo", "receiver", "volume"},
}

FIELD_LABELS = {
    "auto_shipment": {
        "shipment_date": "Дата отгрузки",
        "customer_object": "Объект",
        "vehicle_number": "Машина",
        "driver_name": "Водитель",
        "ttn_number": "Номер ТТН",
        "coal_grade": "Марка угля",
        "quantity": "Количество",
        "carrier": "Перевозчик",
        "comment": "Комментарий",
        "sub_object": "Подобъект",
        "base_code": "База",
        "upd_number": "УПД",
        "balance_note": "Остатки",
        "source_month_text": "Месяц (текст)",
        "source_day_number": "№ дня",
    },
    "rail_shipment": {
        "departure_date": "Дата отправления",
        "wagon_number": "Номер вагона",
        "document_number": "Номер документа",
        "cargo": "Марка угля",
        "destination_station": "Станция назначения РФ",
        "receiver": "Грузополучатель",
        "volume": "Объём",
        "comment": "Комментарий",
        "origin_region": "Область отправления",
        "origin_station": "Станция отправления РФ",
        "sender": "Грузоотправитель",
        "destination_region": "Область назначения",
    },
}

CATALOG_FIELDS = {
    "auto_shipment": {"coal_grade", "base_code"},
    "rail_shipment": {"cargo"},
}


def _cache_key(entity: str) -> str:
    return f"field_config:{entity}"


def get_entity_config(entity: str) -> dict:
    key = _cache_key(entity)
    cached = cache.get(key)
    if cached is not None:
        return cached
    from core.models import FieldSettings
    rows = FieldSettings.objects.filter(entity=entity)
    result = {
        row.field_name: {
            "visible": row.visible,
            "required": row.required,
            "section": row.section,
            "is_system": row.is_system,
            "show_in_list": row.show_in_list,
            "sort_order": row.sort_order,
            "label": row.label,
            "use_catalog": row.use_catalog,
            "allow_filter": row.allow_filter,
            "allow_sort": row.allow_sort,
            "filter_type": row.filter_type,
            "sticky_col": row.sticky_col,
            "preset_membership": row.preset_membership,
        }
        for row in rows
    }
    cache.set(key, result, CACHE_TTL)
    return result


def invalidate_entity_config(entity: str) -> None:
    cache.delete(_cache_key(entity))


def get_filter_config(entity: str) -> dict:
    """Возвращает {field_name: filter_type} для полей с allow_filter=True."""
    config = get_entity_config(entity)
    return {
        name: attrs["filter_type"]
        for name, attrs in config.items()
        if attrs.get("allow_filter") and attrs.get("filter_type", "none") != "none"
    }


def get_sort_fields(entity: str) -> set:
    """Возвращает множество field_name с allow_sort=True."""
    config = get_entity_config(entity)
    return {name for name, attrs in config.items() if attrs.get("allow_sort")}


def load_field_config() -> dict:
    return {
        entity: get_entity_config(entity)
        for entity in ("auto_shipment", "rail_shipment")
    }


ENTITY_PRESETS = {
    "auto_shipment": [
        {"name": "operative", "label": "Оперативный"},
        {"name": "documents", "label": "Документы"},
        {"name": "logistics",  "label": "Логистика"},
        {"name": "full",       "label": "Полный"},
    ],
    "rail_shipment": [
        {"name": "operative", "label": "Оперативный"},
        {"name": "route",     "label": "Маршрут"},
        {"name": "documents", "label": "Документы"},
        {"name": "full",      "label": "Полный"},
    ],
}


def get_table_presets(entity: str) -> list:
    config = get_entity_config(entity)
    preset_defs = ENTITY_PRESETS.get(entity, [])
    result = []
    for p in preset_defs:
        if p["name"] == "full":
            result.append({"name": "full", "label": p["label"], "fields": []})
            continue
        fields = [
            name for name, attrs in config.items()
            if attrs.get("show_in_list") and attrs.get("visible")
            and p["name"] in (attrs.get("preset_membership") or "").split(",")
        ]
        result.append({"name": p["name"], "label": p["label"], "fields": fields})
    return result


def get_sticky_fields(entity: str) -> list:
    config = get_entity_config(entity)
    return [name for name, attrs in config.items() if attrs.get("sticky_col")]
