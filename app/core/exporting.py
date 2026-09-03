from django.conf import settings


class SelectedExportError(ValueError):
    pass


def selected_export_queryset(request, model, order_by):
    raw_ids = request.POST.getlist("ids")
    if not raw_ids:
        raise SelectedExportError("Не выбраны записи для экспорта.")

    selected_ids = []
    seen = set()
    for raw_id in raw_ids:
        value = str(raw_id).strip()
        if not value.isdigit():
            raise SelectedExportError("Некорректный список ID для экспорта.")
        pk = int(value)
        if pk <= 0:
            raise SelectedExportError("Некорректный список ID для экспорта.")
        if pk not in seen:
            selected_ids.append(pk)
            seen.add(pk)

    max_ids = settings.PARTIAL_EXPORT_MAX_IDS
    if len(selected_ids) > max_ids:
        raise SelectedExportError(f"Можно экспортировать не более {max_ids} записей за раз.")

    queryset = model.objects.filter(pk__in=selected_ids, is_deleted=False).order_by(*order_by)
    found_ids = set(queryset.values_list("pk", flat=True))
    if found_ids != set(selected_ids):
        raise SelectedExportError("Часть выбранных записей не найдена или недоступна.")

    return queryset
