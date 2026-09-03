from copy import copy

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models.fields import NOT_PROVIDED

from core.field_config import ENTITY_PRESETS


ENTITY_MODELS = {
    "auto_shipment": ("shipments_auto", "AutoShipment"),
    "rail_shipment": ("shipments_rail", "RailShipment"),
}


TEXT_FIELDS = (models.CharField, models.TextField)
DATE_FIELDS = (models.DateField, models.DateTimeField)
NUMBER_FIELDS = (
    models.IntegerField,
    models.PositiveIntegerField,
    models.PositiveSmallIntegerField,
    models.SmallIntegerField,
    models.BigIntegerField,
    models.FloatField,
    models.DecimalField,
)


def _entity_model(entity):
    app_label, model_name = ENTITY_MODELS[entity]
    return apps.get_model(app_label, model_name)


def _field(model, field_name):
    if "__" in field_name:
        return None
    try:
        field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return None
    return field if isinstance(field, models.Field) else None


def _field_kind(field):
    if isinstance(field, DATE_FIELDS):
        return "date"
    if isinstance(field, NUMBER_FIELDS):
        return "number"
    if isinstance(field, TEXT_FIELDS):
        return "text"
    return "unsupported"


def _label(settings):
    return settings.label or settings.field_name


def _is_model_required(field):
    return not field.blank and not field.null and field.default is NOT_PROVIDED


def validate_field_settings(entity, settings_rows):
    errors = []
    if entity not in ENTITY_MODELS:
        return ["Неизвестная сущность настроек полей."]

    model = _entity_model(entity)
    preset_names = {preset["name"] for preset in ENTITY_PRESETS.get(entity, []) if preset["name"] != "full"}

    for settings in settings_rows:
        label = _label(settings)
        field = _field(model, settings.field_name)
        if field is None:
            errors.append(f"{label}: поле отсутствует в модели.")
            continue

        kind = _field_kind(field)
        model_required = _is_model_required(field)

        if settings.is_system and not settings.visible:
            errors.append(f"{label}: системное поле нельзя скрыть.")
        if model_required and not settings.required:
            errors.append(f"{label}: обязательное поле модели нельзя сделать необязательным.")
        if model_required and not settings.visible:
            errors.append(f"{label}: обязательное поле модели нельзя скрыть.")
        if settings.required and not settings.visible:
            errors.append(f"{label}: обязательное поле нельзя скрыть.")
        if settings.show_in_list and not settings.visible:
            errors.append(f"{label}: скрытое поле нельзя показывать в таблице.")
        if settings.allow_sort and not settings.visible:
            errors.append(f"{label}: скрытое поле нельзя сортировать.")
        if settings.allow_filter and not settings.visible:
            errors.append(f"{label}: скрытое поле нельзя фильтровать.")
        if settings.sticky_col and (not settings.visible or not settings.show_in_list):
            errors.append(f"{label}: sticky разрешён только для видимой табличной колонки.")

        if not settings.allow_filter and settings.filter_type != "none":
            errors.append(f"{label}: тип фильтра задан при выключенной фильтрации.")
        if settings.allow_filter and settings.filter_type == "none":
            errors.append(f"{label}: включена фильтрация без типа фильтра.")

        if settings.filter_type == "value" and kind != "text":
            errors.append(f"{label}: фильтр по значениям разрешён только для текстовых полей.")
        if settings.filter_type == "text" and kind != "text":
            errors.append(f"{label}: текстовый фильтр разрешён только для текстовых полей.")
        if settings.filter_type == "date" and kind != "date":
            errors.append(f"{label}: фильтр по датам разрешён только для дат.")
        if settings.filter_type == "number" and kind != "number":
            errors.append(f"{label}: числовой фильтр разрешён только для числовых полей.")

        memberships = [item for item in (settings.preset_membership or "").split(",") if item]
        unknown_presets = [item for item in memberships if item not in preset_names]
        if unknown_presets:
            errors.append(f"{label}: неизвестные пресеты: {', '.join(unknown_presets)}.")

        if settings.use_catalog and kind != "text":
            errors.append(f"{label}: справочник разрешён только для текстовых полей.")

    return errors


def validate_catalog_enabled(field_settings):
    clone = copy(field_settings)
    clone.use_catalog = True
    return validate_field_settings(clone.entity, [clone])
