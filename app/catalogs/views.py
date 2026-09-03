from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.apps import apps
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.urls import reverse
from .models import CatalogValue
from audit.models import AuditLog
from audit.services import write_audit_log
from core.models import FieldSettings
from core.field_config import invalidate_entity_config
from core.field_settings_validation import validate_catalog_enabled

_ENTITY_LABELS = {
    "auto_shipment": "Автоотгрузки",
    "rail_shipment": "ЖД-отгрузки",
}

_ENTITY_MODEL = {
    "auto_shipment": ("shipments_auto", "AutoShipment"),
    "rail_shipment": ("shipments_rail", "RailShipment"),
}


def _shipment_word(count):
    """Предложный падеж слова «отгрузка»: 1, 21, 101 → «отгрузке», иначе «отгрузках»."""
    if count % 10 == 1 and count % 100 != 11:
        return "отгрузке"
    return "отгрузках"


def _require_perm(user, perm):
    if not user.has_perm(perm):
        raise PermissionDenied


def _catalog_key(entity, field_name):
    return f"{entity}__{field_name}"


def _all_fields_with_catalog_info(entity):
    rows = list(FieldSettings.objects.filter(entity=entity).order_by("sort_order", "field_name"))
    catalog_types = [_catalog_key(fs.entity, fs.field_name) for fs in rows]
    count_rows = CatalogValue.objects.filter(catalog_type__in=catalog_types).values(
        "catalog_type"
    ).annotate(
        total=Count("id"),
        active=Count("id", filter=Q(is_active=True)),
    )
    counts_by_type = {row["catalog_type"]: row for row in count_rows}

    result = []
    for fs in rows:
        ct = _catalog_key(fs.entity, fs.field_name)
        counts = counts_by_type.get(ct, {})
        result.append({
            "fs": fs,
            "catalog_type": ct,
            "entity_label": _ENTITY_LABELS.get(fs.entity, fs.entity),
            "count": counts.get("total", 0),
            "active": counts.get("active", 0),
        })
    return result


def _validated_entity(source):
    entity = source.get("entity", "auto_shipment")
    if entity not in ("auto_shipment", "rail_shipment"):
        entity = "auto_shipment"
    return entity


def _resolve_catalog_type(catalog_type):
    """Разбирает catalog_type вида "<entity>__<field_name>" и проверяет
    существование FieldSettings. Возвращает (entity, field_name, fs) либо
    None, если формат неверен или справочник не найден."""
    try:
        entity, field_name = catalog_type.split("__", 1)
        fs = FieldSettings.objects.get(entity=entity, field_name=field_name)
    except (ValueError, FieldSettings.DoesNotExist):
        return None
    return entity, field_name, fs


@login_required
def catalog_list(request):
    if request.method == "POST":
        _require_perm(request.user, "core.change_fieldsettings")
        entity = _validated_entity(request.POST)
        field_id = request.POST.get("field_id")
        list_url = f"{reverse('catalogs:list')}?entity={entity}"
        try:
            fs = FieldSettings.objects.get(pk=field_id)
        except FieldSettings.DoesNotExist:
            messages.error(request, "Поле не найдено.")
            return redirect(list_url)
        enable_catalog = not fs.use_catalog
        if enable_catalog:
            errors = validate_catalog_enabled(fs)
            if errors:
                for error in errors:
                    messages.error(request, error)
                return redirect(list_url)
        fs.use_catalog = enable_catalog
        fs.save()
        invalidate_entity_config(fs.entity)
        status = "включён" if fs.use_catalog else "отключён"
        label = fs.label or fs.field_name
        messages.success(request, f"Справочник для поля «{label}» {status}.")
        return redirect(list_url)

    _require_perm(request.user, "catalogs.view_catalogvalue")
    entity = _validated_entity(request.GET)
    fields = _all_fields_with_catalog_info(entity)
    return render(request, "catalogs/list.html", {
        "fields": fields,
        "entity": entity,
    })



@login_required
def catalog_values(request, catalog_type):
    _require_perm(request.user, "catalogs.view_catalogvalue")

    resolved = _resolve_catalog_type(catalog_type)
    if resolved is None:
        messages.error(request, "Неизвестный справочник.")
        return redirect("catalogs:list")
    entity, field_name, fs = resolved

    values = CatalogValue.objects.filter(catalog_type=catalog_type).order_by("name")
    label = fs.label or fs.field_name
    return render(request, "catalogs/values.html", {
        "catalog_type": catalog_type,
        "label": label,
        "field_name": field_name,
        "entity": entity,
        "entity_label": _ENTITY_LABELS.get(entity, entity),
        "values": values,
        "use_catalog": fs.use_catalog,
    })


@login_required
def catalog_value_add(request, catalog_type):
    _require_perm(request.user, "catalogs.add_catalogvalue")

    if _resolve_catalog_type(catalog_type) is None:
        messages.error(request, "Неизвестный справочник.")
        return redirect("catalogs:list")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Введите название.")
        else:
            obj, created = CatalogValue.objects.get_or_create(
                catalog_type=catalog_type,
                name=name,
                defaults={"is_active": True},
            )
            if created:
                write_audit_log(
                    entity_type=AuditLog.ENTITY_CATALOG,
                    entity_id=obj.pk,
                    action=AuditLog.ACTION_CREATE,
                    request=request,
                    new_values={"catalog_type": catalog_type, "name": name, "is_active": True},
                    source=AuditLog.SOURCE_UI,
                )
                messages.success(request, f"Добавлено: «{name}».")
            else:
                messages.warning(request, f"Значение «{name}» уже существует.")

    return redirect("catalogs:values", catalog_type=catalog_type)


@login_required
def catalog_value_toggle(request, pk):
    _require_perm(request.user, "catalogs.change_catalogvalue")
    if request.method == "POST":
        obj = get_object_or_404(CatalogValue, pk=pk)
        was_active = obj.is_active
        obj.is_active = not obj.is_active
        obj.save()
        write_audit_log(
            entity_type=AuditLog.ENTITY_CATALOG,
            entity_id=obj.pk,
            action=AuditLog.ACTION_UPDATE,
            request=request,
            old_values={"is_active": was_active},
            new_values={"catalog_type": obj.catalog_type, "name": obj.name, "is_active": obj.is_active},
            source=AuditLog.SOURCE_UI,
        )
        status = "активировано" if obj.is_active else "деактивировано"
        messages.success(request, f"Значение «{obj.name}» {status}.")
        return redirect("catalogs:values", catalog_type=obj.catalog_type)
    return redirect("catalogs:list")


@login_required
def catalog_value_delete(request, pk):
    _require_perm(request.user, "catalogs.delete_catalogvalue")
    if request.method == "POST":
        obj = get_object_or_404(CatalogValue, pk=pk)
        catalog_type = obj.catalog_type
        name = obj.name
        deleted_pk = obj.pk
        was_active = obj.is_active
        obj.delete()
        write_audit_log(
            entity_type=AuditLog.ENTITY_CATALOG,
            entity_id=deleted_pk,
            action=AuditLog.ACTION_DELETE,
            request=request,
            old_values={"catalog_type": catalog_type, "name": name, "is_active": was_active},
            source=AuditLog.SOURCE_UI,
        )
        messages.success(request, f"Значение «{name}» удалено.")
        return redirect("catalogs:values", catalog_type=catalog_type)
    return redirect("catalogs:list")


@login_required
def catalog_value_edit(request, pk):
    _require_perm(request.user, "catalogs.change_catalogvalue")

    obj = get_object_or_404(CatalogValue, pk=pk)
    catalog_type = obj.catalog_type

    resolved = _resolve_catalog_type(catalog_type)
    if resolved is None:
        messages.error(request, "Неизвестный справочник.")
        return redirect("catalogs:list")
    entity, field_name, fs = resolved

    if request.method == "POST":
        new_name = request.POST.get("name", "").strip()
        update_shipments = request.POST.get("update_shipments") == "1"

        if not new_name:
            messages.error(request, "Введите новое название.")
            return redirect("catalogs:edit", pk=pk)

        if new_name == obj.name:
            messages.info(request, "Название не изменилось.")
            return redirect("catalogs:values", catalog_type=catalog_type)

        if CatalogValue.objects.filter(catalog_type=catalog_type, name=new_name).exists():
            messages.error(request, f"Значение «{new_name}» уже существует в этом справочнике.")
            return redirect("catalogs:edit", pk=pk)

        old_name = obj.name
        try:
            with transaction.atomic():
                obj.name = new_name
                obj.save(update_fields=["name"])

                updated_count = 0
                if update_shipments and entity in _ENTITY_MODEL:
                    app_label, model_name = _ENTITY_MODEL[entity]
                    model = apps.get_model(app_label, model_name)
                    # all_objects — затрагиваем и soft-deleted отгрузки, иначе
                    # восстановленная из корзины запись сохранит старое значение,
                    # отсутствующее в справочнике (V12-08).
                    # Нормализация справочника (V12-D2): не трогаем updated_by/updated_at,
                    # чтобы не инвалидировать токены optimistic locking у редакторов.
                    # Атрибуцию даёт сводная запись AuditLog ниже.
                    updated_count = model.all_objects.filter(**{field_name: old_name}).update(
                        **{field_name: new_name},
                    )
        except IntegrityError:
            messages.error(request, f"Не удалось переименовать: значение «{new_name}» уже существует в этом справочнике.")
            return redirect("catalogs:edit", pk=pk)

        # Сводная запись аудита — после успешной транзакции (rollback не оставляет запись).
        write_audit_log(
            entity_type=AuditLog.ENTITY_CATALOG,
            entity_id=obj.pk,
            action=AuditLog.ACTION_CATALOG_RENAME,
            request=request,
            old_values={"catalog_type": catalog_type, "name": old_name},
            new_values={"name": new_name, "shipments_updated": updated_count},
            source=AuditLog.SOURCE_UI,
        )

        if update_shipments and updated_count:
            messages.success(request, f"Значение переименовано: «{old_name}» → «{new_name}». Обновлено отгрузок: {updated_count}.")
        else:
            messages.success(request, f"Значение переименовано: «{old_name}» → «{new_name}».")

        return redirect("catalogs:values", catalog_type=catalog_type)

    # GET — показываем форму с количеством затронутых отгрузок
    shipment_count = 0
    if entity in _ENTITY_MODEL:
        app_label, model_name = _ENTITY_MODEL[entity]
        model = apps.get_model(app_label, model_name)
        # all_objects — предпросмотр совпадает с фактическим обновлением (вкл. soft-deleted).
        shipment_count = model.all_objects.filter(**{field_name: obj.name}).count()

    label = fs.label or field_name

    return render(request, "catalogs/edit_value.html", {
        "obj": obj,
        "catalog_type": catalog_type,
        "label": label,
        "entity_label": _ENTITY_LABELS.get(entity, entity),
        "shipment_count": shipment_count,
        "shipment_word": _shipment_word(shipment_count),
    })
