import json
import logging
import os
import secrets
import tempfile
import datetime
from pathlib import Path
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.urls import reverse
from django.views.generic import View, ListView
from django.shortcuts import render, redirect
from django.contrib import messages
from audit.models import AuditLog
from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment
from core.ip_utils import get_client_ip
from .models import ImportLog, ImportRowResult
from .excel_parser import parse_auto_excel, parse_rail_excel, detect_duplicates

logger = logging.getLogger(__name__)

VALID_SHIPMENT_TYPES = {
    ImportLog.SHIPMENT_TYPE_AUTO,
    ImportLog.SHIPMENT_TYPE_RAIL,
}
# V12-17: задания импорта изолированы по токену (вкладке) внутри одной session,
# чтобы две открытые вкладки не затирали данные предпросмотра друг друга.
IMPORT_JOBS_KEY = "import_jobs"
IMPORT_TOKEN_PARAM = "t"
IMPORT_JOBS_MAX = 5  # потолок параллельных заданий в session; старые подчищаются
IMPORT_TMP_PATTERN = "import_*.json"
IMPORT_BULK_BATCH_SIZE = 500
IMPORT_PREVIEW_PAGE_SIZE = 200


class ImportPermissionMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = "imports.import_shipments"


def _validate_import_file(uploaded_file):
    if not uploaded_file:
        return "Файл не выбран."
    ext = uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else ""
    if ext not in settings.ALLOWED_IMPORT_EXTENSIONS:
        return "Разрешён только формат .xlsx."
    if uploaded_file.size > settings.MAX_IMPORT_SIZE_BYTES:
        return f"Файл превышает допустимый размер {settings.MAX_IMPORT_SIZE_MB} МБ."
    return None


def _is_valid_shipment_type(shipment_type):
    return shipment_type in VALID_SHIPMENT_TYPES


def _bad_request(message="Некорректные параметры запроса."):
    return HttpResponseBadRequest(message)


def _new_import_token():
    return secrets.token_urlsafe(16)


def _store_import_job(request, token, *, tmp_path, shipment_type, filename, year):
    jobs = request.session.get(IMPORT_JOBS_KEY)
    if not isinstance(jobs, dict):
        jobs = {}
    # Прунинг: держим не более IMPORT_JOBS_MAX заданий, удаляя tmp-файлы старых
    # (dict сохраняет порядок вставки — выселяем самые ранние).
    while len(jobs) >= IMPORT_JOBS_MAX:
        old_token, old_job = next(iter(jobs.items()))
        _delete_import_tmp((old_job or {}).get("tmp_path"))
        del jobs[old_token]
    jobs[token] = {
        "tmp_path": tmp_path,
        "type": shipment_type,
        "filename": filename,
        "year": year,
    }
    request.session[IMPORT_JOBS_KEY] = jobs
    request.session.modified = True


def _get_import_job(request, token):
    if not token:
        return None
    jobs = request.session.get(IMPORT_JOBS_KEY)
    if not isinstance(jobs, dict):
        return None
    job = jobs.get(token)
    return job if isinstance(job, dict) else None


def _clear_import_job(request, token, delete_tmp=False):
    jobs = request.session.get(IMPORT_JOBS_KEY)
    if not isinstance(jobs, dict):
        return
    job = jobs.pop(token, None)
    if job is None:
        return
    if delete_tmp:
        _delete_import_tmp(job.get("tmp_path"))
    request.session[IMPORT_JOBS_KEY] = jobs
    request.session.modified = True


class ImportIndexView(ImportPermissionMixin, View):
    def get(self, request):
        return render(request, "imports/index.html")


class ImportUploadView(ImportPermissionMixin, View):
    def get(self, request):
        shipment_type = request.GET.get("type", "auto")
        if not _is_valid_shipment_type(shipment_type):
            return _bad_request("Некорректный тип отгрузок.")
        return render(request, "imports/upload.html", {"shipment_type": shipment_type})

    def post(self, request):
        shipment_type = request.POST.get("shipment_type", "auto")
        if not _is_valid_shipment_type(shipment_type):
            return _bad_request("Некорректный тип отгрузок.")
        file = request.FILES.get("excel_file")

        error = _validate_import_file(file)
        if error:
            messages.error(request, error)
            return render(request, "imports/upload.html", {"shipment_type": shipment_type})

        year = None
        if shipment_type == "auto":
            try:
                year = int(request.POST.get("year", 0))
                if year < 2000 or year > 2100:
                    raise ValueError
            except (ValueError, TypeError):
                messages.error(request, "Укажите корректный год (например, 2025).")
                return render(request, "imports/upload.html", {"shipment_type": shipment_type})

        try:
            if shipment_type == "auto":
                rows, parse_errors = parse_auto_excel(file, year)
            else:
                rows, parse_errors = parse_rail_excel(file)
        except Exception as exc:
            messages.error(request, f"Ошибка чтения файла: {exc}")
            return render(request, "imports/upload.html", {"shipment_type": shipment_type})

        if parse_errors:
            for err in parse_errors:
                messages.error(request, err)
            return render(request, "imports/upload.html", {"shipment_type": shipment_type})

        rows = detect_duplicates(rows, shipment_type)

        _cleanup_import_tmp()

        try:
            serialized_rows = [
                {
                    "row_num": r["row_num"],
                    "data": _serialize_row(r["data"]),
                    "errors": r["errors"],
                    "is_duplicate": r.get("is_duplicate", False),
                    "duplicate_ids": r.get("duplicate_ids", []),
                }
                for r in rows
            ]
            tmp_path = _write_import_tmp(serialized_rows)
        except (OSError, TypeError, ValueError) as exc:
            logger.exception("Не удалось создать temp-файл импорта: %s", exc)
            messages.error(request, "Ошибка сервера при подготовке данных. Попробуйте позже.")
            return render(request, "imports/upload.html", {"shipment_type": shipment_type})

        token = _new_import_token()
        _store_import_job(
            request,
            token,
            tmp_path=tmp_path,
            shipment_type=shipment_type,
            filename=file.name,
            year=year,
        )

        return redirect(f"{reverse('imports:preview')}?{IMPORT_TOKEN_PARAM}={token}")


def _serialize_row(data):
    return {key: _serialize_cell_value(value) for key, value in data.items()}


def _serialize_cell_value(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _deserialize_row(data, shipment_type):
    result = dict(data)
    if shipment_type == "auto" and result.get("shipment_date"):
        result["shipment_date"] = datetime.date.fromisoformat(result["shipment_date"])
    if shipment_type == "auto" and result.get("quantity") is not None:
        result["quantity"] = Decimal(result["quantity"])
    if shipment_type == "rail" and result.get("departure_date"):
        result["departure_date"] = datetime.date.fromisoformat(result["departure_date"])
    if shipment_type == "rail" and result.get("volume") is not None:
        result["volume"] = Decimal(result["volume"])
    return result


def _write_import_tmp(rows_list):
    tmp_dir = Path(settings.IMPORT_TMP_DIR)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".json", prefix="import_", dir=tmp_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows_list, fh)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def _cleanup_import_tmp(ttl_hours=None, now=None, dry_run=False):
    tmp_dir = Path(settings.IMPORT_TMP_DIR)
    if not tmp_dir.exists():
        return {"scanned": 0, "deleted": 0}

    ttl = settings.IMPORT_TMP_TTL_HOURS if ttl_hours is None else ttl_hours
    current_ts = (now or datetime.datetime.now()).timestamp()
    cutoff_ts = current_ts - (ttl * 3600)
    scanned = 0
    deleted = 0

    for path in tmp_dir.glob(IMPORT_TMP_PATTERN):
        try:
            if not path.is_file():
                continue
            scanned += 1
            if path.stat().st_mtime >= cutoff_ts:
                continue
            if not dry_run:
                path.unlink()
            deleted += 1
        except OSError:
            logger.warning("Не удалось удалить temp-файл импорта: %s", path)

    return {"scanned": scanned, "deleted": deleted}


_IMPORT_ROW_REQUIRED_KEYS = frozenset({"row_num", "data", "errors", "is_duplicate"})


def _validate_import_tmp_schema(rows):
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        if not _IMPORT_ROW_REQUIRED_KEYS.issubset(row):
            return False
        if not isinstance(row["data"], dict):
            return False
        if not isinstance(row["errors"], list):
            return False
    return True


def _read_import_tmp(path):
    if not path:
        return None
    # V12-17: путь приходит из session — недоверенный канал для путей ФС.
    # Читаем только файлы из управляемой import-tmp-зоны (та же проверка, что и
    # при удалении), иначе session мог бы указать на произвольный файл.
    if not _is_managed_import_tmp(path):
        logger.warning("Отказ от чтения temp-файла импорта вне разрешённых путей: %s", path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rows = json.load(fh)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        logger.warning("Temp-файл импорта не найден или повреждён: %s", path)
        return None
    if not _validate_import_tmp_schema(rows):
        logger.warning("Temp-файл импорта имеет неверную схему: %s", path)
        return None
    return rows


def _delete_import_tmp(path):
    if not path:
        return
    if not _is_managed_import_tmp(path):
        logger.warning("Отказ от удаления temp-файла импорта вне разрешённых путей: %s", path)
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _is_managed_import_tmp(path):
    try:
        target = Path(path).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    if target.name[:7] != "import_" or target.suffix != ".json":
        return False

    managed_dir = Path(settings.IMPORT_TMP_DIR).resolve(strict=False)
    if target.is_relative_to(managed_dir):
        return True

    legacy_tmp_dir = Path(tempfile.gettempdir()).resolve(strict=False)
    return target.parent == legacy_tmp_dir


def _create_imported_shipment(data, shipment_type, request, filename):
    if shipment_type == "auto":
        obj = AutoShipment.objects.create(
            shipment_date=data.get("shipment_date"),
            source_month_text=data.get("source_month_text") or "",
            source_day_number=data.get("source_day_number"),
            customer_object=data.get("customer_object") or "",
            sub_object=data.get("sub_object") or "",
            vehicle_number=data.get("vehicle_number") or "",
            driver_name=data.get("driver_name") or "",
            ttn_number=str(data.get("ttn_number") or ""),
            coal_grade=data.get("coal_grade") or "",
            quantity=data.get("quantity") or 0,
            base_code=data.get("base_code") or "",
            upd_number=str(data.get("upd_number") or ""),
            carrier=data.get("carrier") or "",
            balance_note=data.get("balance_note") or "",
            created_by=request.user,
            updated_by=request.user,
        )
    else:
        obj = RailShipment.objects.create(
            departure_date=data.get("departure_date"),
            wagon_number=data.get("wagon_number") or "",
            document_number=data.get("document_number") or "",
            cargo=data.get("cargo") or "",
            origin_region=data.get("origin_region") or "",
            origin_station=data.get("origin_station") or "",
            sender=data.get("sender") or "",
            destination_region=data.get("destination_region") or "",
            destination_station=data.get("destination_station") or "",
            receiver=data.get("receiver") or "",
            volume=data.get("volume") or 0,
            created_by=request.user,
            updated_by=request.user,
        )
    return obj


def _get_import_audit_entity_type(shipment_type):
    if shipment_type == "auto":
        return AuditLog.ENTITY_AUTO
    return AuditLog.ENTITY_RAIL


def _build_import_audit_log(obj, shipment_type, filename, user, ip_address, user_agent):
    return AuditLog(
        entity_type=_get_import_audit_entity_type(shipment_type),
        entity_id=obj.pk,
        action=AuditLog.ACTION_CREATE,
        new_values={"filename": filename},
        source=AuditLog.SOURCE_IMPORT,
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def _get_import_status(imported, skipped, duplicates, errors):
    if imported == 0 and errors > 0:
        return ImportLog.STATUS_ERROR
    if skipped > 0 or duplicates > 0 or errors > 0:
        return ImportLog.STATUS_PARTIAL
    return ImportLog.STATUS_SUCCESS


def _create_initial_import_log(shipment_type, filename, total_rows, user):
    return ImportLog.objects.create(
        shipment_type=shipment_type,
        filename=filename,
        status=ImportLog.STATUS_ERROR,
        total_rows=total_rows,
        imported_rows=0,
        updated_rows=0,
        skipped_rows=0,
        error_rows=0,
        duplicate_rows=0,
        created_by=user,
    )


def _append_import_row_result(
    row_results,
    counters,
    log,
    row,
    status,
    counter_field,
    messages=None,
    created_object_id=None,
    created_object_label="",
):
    row_results.append(ImportRowResult(
        import_log=log,
        row_num=row["row_num"],
        status=status,
        messages=messages or [],
        source_data=row.get("data", {}),
        created_object_id=created_object_id,
        created_object_label=created_object_label,
    ))
    counters[counter_field] += 1


def _empty_import_counters():
    return {
        "imported_rows": 0,
        "updated_rows": 0,
        "skipped_rows": 0,
        "error_rows": 0,
        "duplicate_rows": 0,
    }


def _process_import_row(log, row, import_ids, shipment_type, request, filename, batch):
    if row["is_duplicate"]:
        ids = row.get("duplicate_ids", [])
        if ids:
            id_str = ", ".join(str(i) for i in sorted(ids)[:5])
            msg = f"Похожая запись уже есть в системе (ID: {id_str})."
        else:
            msg = "Похожая запись уже есть в системе."
        _append_import_row_result(
            batch["row_results"],
            batch["counters"],
            log,
            row,
            ImportRowResult.STATUS_DUPLICATE,
            "duplicate_rows",
            messages=[msg],
        )
        return

    if row["errors"]:
        _append_import_row_result(
            batch["row_results"],
            batch["counters"],
            log,
            row,
            ImportRowResult.STATUS_ERROR,
            "error_rows",
            messages=row["errors"],
        )
        return

    row_key = str(row["row_num"])
    if row_key not in import_ids:
        _append_import_row_result(
            batch["row_results"],
            batch["counters"],
            log,
            row,
            ImportRowResult.STATUS_SKIPPED,
            "skipped_rows",
            messages=["Строка снята пользователем в предпросмотре."],
        )
        return

    try:
        data = _deserialize_row(row["data"], shipment_type)
        with transaction.atomic():
            obj = _create_imported_shipment(data, shipment_type, request, filename)
        batch["audit_logs"].append(_build_import_audit_log(
            obj,
            shipment_type,
            filename,
            request.user,
            batch["ip_address"],
            batch["user_agent"],
        ))
        _append_import_row_result(
            batch["row_results"],
            batch["counters"],
            log,
            row,
            ImportRowResult.STATUS_CREATED,
            "imported_rows",
            created_object_id=obj.pk,
            created_object_label=str(obj),
        )
    except Exception:
        logger.exception("Ошибка при импорте строки %s из файла %r", row.get("row_num"), filename)
        _append_import_row_result(
            batch["row_results"],
            batch["counters"],
            log,
            row,
            ImportRowResult.STATUS_ERROR,
            "error_rows",
            messages=["Ошибка создания записи. Подробности записаны в лог приложения."],
        )


def _apply_import_counters(log, counters):
    log.imported_rows = counters["imported_rows"]
    log.updated_rows = counters["updated_rows"]
    log.skipped_rows = counters["skipped_rows"]
    log.error_rows = counters["error_rows"]
    log.duplicate_rows = counters["duplicate_rows"]
    log.status = _get_import_status(
        log.imported_rows,
        log.skipped_rows,
        log.duplicate_rows,
        log.error_rows,
    )


def _save_final_import_log(log):
    log.save(update_fields=[
        "imported_rows",
        "updated_rows",
        "skipped_rows",
        "error_rows",
        "duplicate_rows",
        "status",
    ])


def _process_import_rows(log, rows, import_ids, shipment_type, request, filename):
    batch = {
        "row_results": [],
        "audit_logs": [],
        "counters": _empty_import_counters(),
        "ip_address": get_client_ip(request),
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
    }

    with transaction.atomic():
        for row in rows:
            _process_import_row(log, row, import_ids, shipment_type, request, filename, batch)

        if batch["audit_logs"]:
            AuditLog.objects.bulk_create(
                batch["audit_logs"],
                batch_size=IMPORT_BULK_BATCH_SIZE,
            )
        if batch["row_results"]:
            ImportRowResult.objects.bulk_create(
                batch["row_results"],
                batch_size=IMPORT_BULK_BATCH_SIZE,
            )

        _apply_import_counters(log, batch["counters"])
        _save_final_import_log(log)


def _selected_valid_import_rows(rows, import_ids):
    return [
        row for row in rows
        if (
            str(row["row_num"]) in import_ids
            and not row.get("is_duplicate")
            and not row.get("errors")
        )
    ]


def _mark_import_batch_error(log, rows, import_ids):
    recovery_results = []
    counters = _empty_import_counters()
    for row in _selected_valid_import_rows(rows, import_ids):
        _append_import_row_result(
            recovery_results,
            counters,
            log,
            row,
            ImportRowResult.STATUS_ERROR,
            "error_rows",
            messages=["Ошибка сохранения результатов импорта. Подробности записаны в лог приложения."],
        )

    _apply_import_counters(log, counters)
    log.status = ImportLog.STATUS_ERROR
    _save_final_import_log(log)

    if recovery_results:
        ImportRowResult.objects.bulk_create(
            recovery_results,
            batch_size=IMPORT_BULK_BATCH_SIZE,
        )


class ImportPreviewView(ImportPermissionMixin, View):
    def get(self, request):
        token = request.GET.get(IMPORT_TOKEN_PARAM)
        job = _get_import_job(request, token)
        if job is None:
            messages.error(request, "Нет данных для предпросмотра. Загрузите файл заново.")
            return redirect("imports:upload")

        shipment_type = job.get("type", "auto")
        if not _is_valid_shipment_type(shipment_type):
            _clear_import_job(request, token, delete_tmp=True)
            return _bad_request("Некорректный тип отгрузок.")

        rows = _read_import_tmp(job.get("tmp_path"))
        if rows is None:
            _clear_import_job(request, token)
            messages.error(request, "Нет данных для предпросмотра. Загрузите файл заново.")
            return redirect("imports:upload")

        valid_rows = [r for r in rows if not r["errors"] and not r["is_duplicate"]]
        error_rows = [r for r in rows if r["errors"]]
        duplicate_rows = [r for r in rows if not r["errors"] and r["is_duplicate"]]
        preview_rows = {
            "valid": valid_rows,
            "duplicates": duplicate_rows,
            "errors": error_rows,
        }

        return render(request, "imports/preview.html", {
            "rows": rows,
            "valid_rows": valid_rows,
            "error_rows": error_rows,
            "duplicate_rows": duplicate_rows,
            "valid_count": len(valid_rows),
            "error_count": len(error_rows),
            "duplicate_count": len(duplicate_rows),
            "preview_rows": preview_rows,
            "import_preview_page_size": IMPORT_PREVIEW_PAGE_SIZE,
            "auto_detail_url_template": reverse("auto:detail", args=[0]).replace("/0/", "/__id__/"),
            "rail_detail_url_template": reverse("rail:detail", args=[0]).replace("/0/", "/__id__/"),
            "shipment_type": shipment_type,
            "filename": job.get("filename", ""),
            "total": len(rows),
            "import_token": token,
        })

    def post(self, request):
        token = request.POST.get(IMPORT_TOKEN_PARAM)
        job = _get_import_job(request, token)
        if job is None:
            messages.error(request, "Сессия устарела, загрузите файл заново.")
            return redirect("imports:upload")

        shipment_type = job.get("type", "auto")
        filename = job.get("filename", "")
        tmp_path = job.get("tmp_path")
        if not _is_valid_shipment_type(shipment_type):
            _clear_import_job(request, token, delete_tmp=True)
            return _bad_request("Некорректный тип отгрузок.")

        rows = _read_import_tmp(tmp_path)
        if rows is None:
            _clear_import_job(request, token)
            messages.error(request, "Сессия устарела, загрузите файл заново.")
            return redirect("imports:upload")

        if not request.POST.get("selection_submitted"):
            messages.error(request, "Сессия устарела, загрузите файл заново.")
            return redirect("imports:upload")

        rows = detect_duplicates(rows, shipment_type)

        import_ids = set(request.POST.getlist("import_ids"))
        if not import_ids:
            messages.warning(request, "Выберите хотя бы одну строку для импорта.")
            # файл НЕ удаляется — пользователь вернётся на свой preview по токену
            return redirect(f"{reverse('imports:preview')}?{IMPORT_TOKEN_PARAM}={token}")

        # V19-LOW-3: токен снимается сразу после всех валидаций и до обработки
        # строк, чтобы double-click/повторная вкладка с тем же токеном не смогли
        # обработать одни и те же строки дважды. tmp-файл сохраняем по пути ниже
        # (в session job его уже не будет), удаляем в finally независимо от токена.
        # session.save() — принудительно, немедленно: обычное сохранение session
        # middleware делает только в process_response (после return), а второй
        # запрос с тем же токеном может прийти раньше.
        _clear_import_job(request, token)
        request.session.save()

        try:
            log = _create_initial_import_log(shipment_type, filename, len(rows), request.user)
            try:
                _process_import_rows(log, rows, import_ids, shipment_type, request, filename)
            except Exception:
                logger.exception("Ошибка batch-записи результатов импорта файла %r", filename)
                _mark_import_batch_error(log, rows, import_ids)

            return redirect("imports:result", pk=log.pk)
        finally:
            _delete_import_tmp(tmp_path)


class ImportResultView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "imports.view_importlog"

    def get(self, request, pk):
        try:
            log = ImportLog.objects.prefetch_related("row_results").get(pk=pk)
        except ImportLog.DoesNotExist:
            messages.error(request, "Запись журнала не найдена.")
            return redirect("imports:log")
        return render(request, "imports/result.html", {"log": log})


class ImportLogView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "imports.view_importlog"
    model = ImportLog
    template_name = "imports/log.html"
    context_object_name = "logs"
    paginate_by = 25
