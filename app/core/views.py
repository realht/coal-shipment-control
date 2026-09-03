from collections import defaultdict
from decimal import Decimal
import logging
from urllib.parse import urlencode

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.conf import settings
from django.db import transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_time
from pathlib import Path
from audit.models import AuditLog
from audit.services import write_audit_log
from imports.models import ImportLog
from shipments_auto.models import AutoShipment
from shipments_rail.models import RailShipment
from documents.models import ShipmentDocument
from .models import BackupRun, BackupSchedule, RestoreRun, SystemState, FieldSettings
from .field_config import invalidate_entity_config, ENTITY_PRESETS
from .dashboard_service import get_dashboard_stats
from .shipment_views import shipment_to_dict, log_shipment
from .table_filters import parse_top_level_date_bound
from .field_settings_validation import validate_field_settings
from .system_ops import (
    database_health,
    delete_backup_by_key,
    get_backup_dir,
    get_backup_delete_preview,
    get_backup_entry_by_key,
    get_media_root,
    get_readiness_status,
    recalculate_uploads_size,
    get_active_operations,
    get_system_state,
    get_system_state_readonly,
    has_active_operation,
    recover_interrupted_restore,
    scan_backup_manifests,
    set_system_mode,
)

logger = logging.getLogger(__name__)


@login_required
def index(request):
    can_view_auto = request.user.has_perm("shipments_auto.view_autoshipment")
    can_view_rail = request.user.has_perm("shipments_rail.view_railshipment")
    ctx = {
        "can_add": (
            request.user.has_perm("shipments_auto.add_autoshipment")
            or request.user.has_perm("shipments_rail.add_railshipment")
        ),
        "can_export": (
            request.user.has_perm("shipments_auto.export_excel")
            or request.user.has_perm("shipments_rail.export_excel")
        ),
        "user_groups": list(request.user.groups.values_list("name", flat=True)),
    }
    ctx.update(get_dashboard_stats(can_view_auto, can_view_rail))
    return render(request, "core/index.html", ctx)


AUTO_DUPLICATE_FIELDS = [
    "shipment_date", "vehicle_number", "driver_name",
    "ttn_number", "coal_grade", "quantity", "customer_object",
]

RAIL_DUPLICATE_FIELDS = [
    "departure_date", "wagon_number", "document_number",
    "cargo", "receiver", "volume",
]


MAX_DUPLICATE_RECORDS = 5000


def _duplicate_compare_value(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().casefold()
    if isinstance(value, Decimal):
        return value.normalize()
    return str(value)


def _find_duplicates(model, fields, date_field, date_from, date_to):
    qs = model.objects.filter(is_deleted=False)
    if date_from:
        qs = qs.filter(**{f"{date_field}__gte": date_from})
    if date_to:
        qs = qs.filter(**{f"{date_field}__lte": date_to})

    # Pass 1: загружаем только поля сравнения — лёгкие dict, не ORM-объекты
    rows = list(qs.values("id", *fields).order_by(date_field, "id")[:MAX_DUPLICATE_RECORDS + 1])
    if len(rows) > MAX_DUPLICATE_RECORDS:
        raise ValueError(
            f"Слишком много записей для поиска дублей (>{MAX_DUPLICATE_RECORDS}). "
            "Уточните диапазон дат."
        )

    groups: dict[tuple, list[int]] = defaultdict(list)
    for row in rows:
        key = tuple(_duplicate_compare_value(row[f]) for f in fields)
        groups[key].append(row["id"])

    id_clusters = [
        ids
        for key, ids in groups.items()
        if len(ids) >= 2 and sum(1 for v in key if v != "") >= 3
    ]

    if not id_clusters:
        return []

    # Pass 2: загружаем полные объекты только для найденных дублей (<<50 штук)
    all_dup_ids = [pk for cluster in id_clusters for pk in cluster]
    objects_by_id = {obj.pk: obj for obj in model.objects.filter(pk__in=all_dup_ids)}
    return [
        [objects_by_id[pk] for pk in cluster if pk in objects_by_id]
        for cluster in id_clusters
    ]


@login_required
def duplicates(request):
    can_view_auto = request.user.has_perm("shipments_auto.view_autoshipment")
    can_view_rail = request.user.has_perm("shipments_rail.view_railshipment")
    if not can_view_auto and not can_view_rail:
        messages.error(request, "У вас нет прав для просмотра этой страницы.")
        return redirect("index")

    shipment_type = request.GET.get("type", "auto")
    if shipment_type not in ("auto", "rail"):
        return HttpResponseBadRequest("Некорректный тип отгрузок.")
    date_from_raw = request.GET.get("date_from", "")
    date_to_raw = request.GET.get("date_to", "")
    date_from = parse_top_level_date_bound(date_from_raw)
    date_to = parse_top_level_date_bound(date_to_raw)

    # POST branch first — before any expensive search
    if request.method == "POST":
        pk_raw = request.POST.get("pk")
        stype = request.POST.get("shipment_type", "auto")
        if stype not in ("auto", "rail"):
            return HttpResponseBadRequest("Некорректный тип отгрузок.")
        try:
            pk = int(pk_raw)
            if pk <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Некорректный идентификатор записи.")

        redirect_params = {"type": stype}
        if request.POST.get("date_from"):
            redirect_params["date_from"] = request.POST["date_from"]
        if request.POST.get("date_to"):
            redirect_params["date_to"] = request.POST["date_to"]
        redirect_url = f"/duplicates/?{urlencode(redirect_params)}"

        if stype == "auto":
            can_delete = request.user.has_perm("shipments_auto.delete_autoshipment")
            if not can_delete:
                messages.error(request, "У вас нет прав на удаление автоотгрузок.")
                return redirect(redirect_url)
            try:
                obj = AutoShipment.objects.get(pk=pk)
            except AutoShipment.DoesNotExist:
                messages.error(request, "Запись не найдена.")
                return redirect(redirect_url)
            old = shipment_to_dict(obj)
            with transaction.atomic():
                obj.delete()
                log_shipment(request, AuditLog.ENTITY_AUTO, AuditLog.ACTION_DELETE, obj, old=old)
        else:
            can_delete = request.user.has_perm("shipments_rail.delete_railshipment")
            if not can_delete:
                messages.error(request, "У вас нет прав на удаление ЖД-отгрузок.")
                return redirect(redirect_url)
            try:
                obj = RailShipment.objects.get(pk=pk)
            except RailShipment.DoesNotExist:
                messages.error(request, "Запись не найдена.")
                return redirect(redirect_url)
            old = shipment_to_dict(obj)
            with transaction.atomic():
                obj.delete()
                log_shipment(request, AuditLog.ENTITY_RAIL, AuditLog.ACTION_DELETE, obj, old=old)

        messages.success(request, f"Запись #{pk} удалена.")
        return redirect(redirect_url)

    searched = "type" in request.GET
    clusters = []
    if searched:
        try:
            if shipment_type == "auto":
                if not can_view_auto:
                    messages.error(request, "У вас нет прав на просмотр автоотгрузок.")
                    return redirect("/duplicates/?type=rail")
                clusters = _find_duplicates(AutoShipment, AUTO_DUPLICATE_FIELDS, "shipment_date", date_from, date_to)
            else:
                if not can_view_rail:
                    messages.error(request, "У вас нет прав на просмотр ЖД-отгрузок.")
                    return redirect("/duplicates/?type=auto")
                clusters = _find_duplicates(RailShipment, RAIL_DUPLICATE_FIELDS, "departure_date", date_from, date_to)
        except ValueError as e:
            messages.error(request, str(e))

    return render(request, "core/duplicates.html", {
        "shipment_type": shipment_type,
        "date_from": date_from_raw,
        "date_to": date_to_raw,
        "searched": searched,
        "clusters": clusters,
        "can_view_auto": can_view_auto,
        "can_view_rail": can_view_rail,
        "can_delete_auto": request.user.has_perm("shipments_auto.delete_autoshipment"),
        "can_delete_rail": request.user.has_perm("shipments_rail.delete_railshipment"),
    })


def healthz(request):
    return JsonResponse({"status": "ok"})


@login_required
def health(request):
    try:
        database_health()
    except Exception:
        logger.exception("Database health check failed")
        return JsonResponse({"status": "error", "db": False}, status=503)
    return JsonResponse({"status": "ok", "db": True})


def readyz(request):
    result = get_readiness_status()
    status_code = 503 if result["status"] == "error" else 200
    return JsonResponse(result, status=status_code)


def _require_perm(user, perm):
    if not user.has_perm(perm):
        raise PermissionDenied


def _backup_files_deleted(run):
    return bool(
        run.status == BackupRun.STATUS_SUCCESS
        and run.manifest_path
        and not Path(run.manifest_path).exists()
    )


def _system_context(user):
    backup_entries = scan_backup_manifests()
    full_entries = [
        item for item in backup_entries
        if item["backup_type"] in (BackupRun.TYPE_FULL, BackupRun.TYPE_PRE_RESTORE)
    ]
    incremental_entries = [
        item for item in backup_entries
        if item["backup_type"] == BackupRun.TYPE_INCREMENTAL
    ]
    try:
        db_ok = database_health()
        db_error = ""
    except Exception as exc:
        db_ok = False
        db_error = str(exc)

    backup_dir = get_backup_dir()
    media_root = get_media_root()
    User = get_user_model()
    active_operations = get_active_operations()
    running_backup = active_operations["backup_runs"].filter(status=BackupRun.STATUS_RUNNING).first()
    running_restore = active_operations["restore_runs"].filter(status=RestoreRun.STATUS_RUNNING).first()
    active_running_operation_label = ""
    if running_restore:
        active_running_operation_label = f"Restore #{running_restore.pk}"
    elif running_backup:
        active_running_operation_label = f"Backup #{running_backup.pk}"
    schedule_weekday_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    backup_schedules = []
    for schedule in BackupSchedule.objects.select_related("last_run").order_by("backup_type"):
        backup_schedules.append({
            "object": schedule,
            "weekday_labels": [
                schedule_weekday_labels[idx] for idx in schedule.weekday_numbers()
            ],
            "weekday_numbers": [str(idx) for idx in schedule.weekday_numbers()],
        })
    backup_runs = list(
        BackupRun.objects.select_related("initiated_by").order_by("-created_at")[:20]
    )
    restore_runs = list(
        RestoreRun.objects.select_related("initiated_by", "pre_restore_backup")
        .order_by("-created_at")[:20]
    )
    manifest_paths = {r.full_manifest_path for r in restore_runs if r.full_manifest_path}
    manifest_paths |= {r.incremental_manifest_path for r in restore_runs if r.incremental_manifest_path}
    backup_by_manifest = {
        b.manifest_path: b
        for b in BackupRun.objects.filter(manifest_path__in=manifest_paths).order_by("pk")
    }
    operation_runs = []
    for run in backup_runs:
        operation_runs.append({
            "kind": "backup",
            "run": run,
            "timestamp": run.finished_at or run.started_at or run.created_at,
            "initiated_by_display": run.initiated_by.username if run.initiated_by else None,
            "source_display": run.get_source_display(),
            "backup_files_deleted": _backup_files_deleted(run),
        })
    for run in restore_runs:
        full_src = backup_by_manifest.get(run.full_manifest_path)
        incr_src = backup_by_manifest.get(run.incremental_manifest_path) if run.incremental_manifest_path else None
        pre = run.pre_restore_backup
        operation_runs.append({
            "kind": "restore",
            "run": run,
            "timestamp": run.finished_at or run.started_at or run.created_at,
            "initiated_by_display": run.initiated_by.username if run.initiated_by else None,
            "full_source_backup": full_src,
            "full_source_comment": full_src.comment if full_src else "",
            "full_source_files_deleted": _backup_files_deleted(full_src) if full_src else False,
            "incr_source_backup": incr_src,
            "incr_source_comment": incr_src.comment if incr_src else "",
            "incr_source_files_deleted": _backup_files_deleted(incr_src) if incr_src else False,
            "pre_restore_backup": pre,
            "pre_restore_files_deleted": _backup_files_deleted(pre) if pre else False,
        })
    operation_runs.sort(key=lambda item: item["timestamp"], reverse=True)

    state = get_system_state_readonly() or SystemState(singleton_key=1)
    return {
        "system_state": state,
        "auto_count": AutoShipment.objects.filter(is_deleted=False).count(),
        "rail_count": RailShipment.objects.filter(is_deleted=False).count(),
        "document_count": ShipmentDocument.objects.filter(is_deleted=False).count(),
        "user_count": User.objects.count(),
        "import_errors": ImportLog.objects.filter(
            status__in=[ImportLog.STATUS_ERROR, ImportLog.STATUS_PARTIAL]
        ).order_by("-created_at")[:5],
        "uploads_size_bytes": state.uploads_size_bytes,
        "uploads_size_calculated_at": state.uploads_size_calculated_at,
        "uploads_path": str(media_root),
        "backup_dir": str(backup_dir),
        "backup_dir_exists": backup_dir.exists(),
        "backup_entries": backup_entries[:20],
        "full_entries": full_entries,
        "incremental_entries": incremental_entries,
        "incremental_entries_json": [
            {
                "key": item["key"],
                "created_at": item["created_at"],
                "baseline_manifest": item["baseline_manifest"],
            }
            for item in incremental_entries
        ],
        "operation_runs": operation_runs[:10],
        "db_ok": db_ok,
        "db_error": db_error,
        "app_version": getattr(settings, "APP_VERSION", ""),
        "app_build_id": getattr(settings, "APP_BUILD_ID", ""),
        "app_git_commit_short": getattr(settings, "APP_GIT_COMMIT", "")[:12],
        "app_built_at": getattr(settings, "APP_BUILT_AT", ""),
        "deployed_at": getattr(settings, "DEPLOYED_AT", ""),
        "active_backup_runs": active_operations["backup_runs"][:5],
        "active_restore_runs": active_operations["restore_runs"][:5],
        "active_operation": active_operations["backup_runs"].exists() or active_operations["restore_runs"].exists(),
        "active_running_operation_label": active_running_operation_label,
        "backup_schedules": backup_schedules,
        "schedule_weekday_options": list(enumerate(schedule_weekday_labels)),
        "MODE_NORMAL": SystemState.MODE_NORMAL,
        "MODE_ADMIN_ONLY": SystemState.MODE_ADMIN_ONLY,
        "MODE_RESTORE_RUNNING": SystemState.MODE_RESTORE_RUNNING,
        "can_change_system_mode": user.has_perm("core.change_system_mode"),
        "can_recover_system_operations": user.has_perm("core.recover_system_operations"),
        "can_run_backup": user.has_perm("core.run_backup"),
        "can_run_restore": user.has_perm("core.run_restore"),
        "scheduler_heartbeat_at": state.scheduler_heartbeat_at,
        "scheduler_is_stale": (
            state.scheduler_heartbeat_at is None
            or (timezone.now() - state.scheduler_heartbeat_at).total_seconds()
            > getattr(settings, "SCHEDULER_WARN_SECONDS", 180)
        ),
    }


@login_required
@permission_required("core.view_system_status", raise_exception=True)
def system_status(request):
    return render(request, "core/system.html", _system_context(request.user))


@login_required
@permission_required("core.run_backup", raise_exception=True)
def update_backup_schedule(request):
    if request.method != "POST":
        return redirect("core:system_status")

    valid_weekdays = {str(idx) for idx in range(7)}

    # Фаза 1: провалидировать все расписания целиком. Любая ошибка прерывает
    # запрос до записи в БД, чтобы не оставить частично применённое состояние (V12-24).
    pending = []
    for schedule in BackupSchedule.objects.order_by("backup_type"):
        enabled = f"enabled_{schedule.pk}" in request.POST
        weekdays = [
            item for item in request.POST.getlist(f"weekdays_{schedule.pk}")
            if item in valid_weekdays
        ]
        run_time = parse_time(request.POST.get(f"run_time_{schedule.pk}", ""))
        if enabled and not weekdays:
            messages.error(request, "Для включённого расписания выберите хотя бы один день недели.")
            return redirect("core:system_status")
        if run_time is None:
            messages.error(request, "Укажите корректное время запуска backup.")
            return redirect("core:system_status")
        pending.append((schedule, enabled, weekdays, run_time))

    # Фаза 2: применить все изменения и аудит в одной транзакции.
    with transaction.atomic():
        for schedule, enabled, weekdays, run_time in pending:
            old_values = {
                "backup_type": schedule.backup_type,
                "enabled": schedule.enabled,
                "weekdays": schedule.weekdays,
                "run_time": str(schedule.run_time),
                "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else "",
            }
            schedule.enabled = enabled
            schedule.weekdays = ",".join(weekdays)
            schedule.run_time = run_time
            schedule.next_run_at = schedule.calculate_next_run()
            schedule.save(update_fields=["enabled", "weekdays", "run_time", "next_run_at"])
            write_audit_log(
                entity_type=AuditLog.ENTITY_SYSTEM,
                entity_id=schedule.pk,
                action=AuditLog.ACTION_BACKUP_SCHEDULE_UPDATED,
                request=request,
                source=AuditLog.SOURCE_UI,
                old_values=old_values,
                new_values={
                    "backup_type": schedule.backup_type,
                    "enabled": schedule.enabled,
                    "weekdays": schedule.weekdays,
                    "run_time": str(schedule.run_time),
                    "next_run_at": schedule.next_run_at.isoformat() if schedule.next_run_at else "",
                },
            )

    messages.success(request, "Расписание backup сохранено.")
    return redirect("core:system_status")


@login_required
@permission_required("core.change_system_mode", raise_exception=True)
def system_maintenance(request):
    if request.method != "POST":
        return redirect("core:system_status")

    mode = request.POST.get("mode")
    reason = request.POST.get("reason", "").strip()
    if mode not in (SystemState.MODE_NORMAL, SystemState.MODE_ADMIN_ONLY):
        messages.error(request, "Некорректный режим обслуживания.")
        return redirect("core:system_status")
    if mode == SystemState.MODE_ADMIN_ONLY and not reason:
        messages.error(request, "Укажите причину профилактики.")
        return redirect("core:system_status")
    if mode == SystemState.MODE_NORMAL and get_system_state().mode == SystemState.MODE_RESTORE_RUNNING:
        messages.error(request, "Нельзя выключить режим восстановления вручную во время restore.")
        return redirect("core:system_status")

    set_system_mode(mode, request.user, reason, request=request)
    if mode == SystemState.MODE_ADMIN_ONLY:
        messages.success(request, "Режим профилактики включён.")
    else:
        messages.success(request, "Обычный режим включён.")
    return redirect("core:system_status")


@login_required
@permission_required("core.recover_system_operations", raise_exception=True)
def recover_restore(request):
    if request.method != "POST":
        return redirect("core:system_status")

    state = get_system_state()
    if state.mode != SystemState.MODE_RESTORE_RUNNING and not has_active_operation():
        messages.info(request, "Зависшие операции не найдены.")
        return redirect("core:system_status")

    result = recover_interrupted_restore(request.user, request=request)
    if result.get("recovery_refused"):
        messages.info(
            request,
            (
                "Scheduler недавно обновлял heartbeat; активная операция не считается "
                "зависшей. Повторите сброс позже или остановите scheduler перед ручным сбросом."
            ),
        )
        return redirect("core:system_status")
    if not result["backup_count"] and not result["restore_count"] and not result["mode_was_stuck"]:
        messages.info(
            request,
            (
                "Активные операции ещё не считаются зависшими. "
                "Повторите сброс позже, если scheduler или контейнер действительно были остановлены."
            ),
        )
        return redirect("core:system_status")

    id_parts = []
    if result["affected_backup_ids"]:
        id_parts.append("backup #" + ", #".join(str(x) for x in result["affected_backup_ids"]))
    if result["affected_restore_ids"]:
        id_parts.append("restore #" + ", #".join(str(x) for x in result["affected_restore_ids"]))

    if result["mode_was_stuck"] and not id_parts:
        messages.warning(
            request,
            (
                "Режим восстановления сброшен в профилактику: активных операций не найдено. "
                "Проверьте состояние данных вручную перед возвратом в normal."
            ),
        )
    else:
        id_str = "; ".join(id_parts) if id_parts else "активных операций не найдено"
        messages.warning(
            request,
            (
                f"Зависшие операции остановлены ({id_str}). "
                "Система оставлена в режиме профилактики для проверки."
            ),
        )
    return redirect("core:system_status")


@login_required
@permission_required("core.view_system_status", raise_exception=True)
def recalculate_uploads_size_view(request):
    if request.method != "POST":
        return redirect("core:system_status")
    recalculate_uploads_size()
    messages.success(request, "Размер uploads пересчитан.")
    return redirect("core:system_status")


@login_required
@permission_required("core.run_backup", raise_exception=True)
def start_backup(request):
    if request.method != "POST":
        return redirect("core:system_status")

    with transaction.atomic():
        state, _ = SystemState.objects.select_for_update().get_or_create(singleton_key=1)
        if state.mode != SystemState.MODE_ADMIN_ONLY:
            messages.error(request, "Backup из UI доступен только в режиме профилактики.")
            return redirect("core:system_status")
        if has_active_operation():
            messages.error(request, "Уже выполняется backup или restore.")
            return redirect("core:system_status")

        backup_type = request.POST.get("backup_type")
        if backup_type not in (BackupRun.TYPE_FULL, BackupRun.TYPE_INCREMENTAL):
            messages.error(request, "Некорректный тип backup.")
            return redirect("core:system_status")

        comment = request.POST.get("comment", "").strip()[:500]
        run = BackupRun.objects.create(
            backup_type=backup_type,
            status=BackupRun.STATUS_QUEUED,
            initiated_by=request.user,
            comment=comment,
            source=BackupRun.SOURCE_UI,
        )
        write_audit_log(
            entity_type=AuditLog.ENTITY_BACKUP,
            entity_id=run.pk,
            action=AuditLog.ACTION_BACKUP_QUEUED,
            request=request,
            source=AuditLog.SOURCE_UI,
            new_values={"backup_type": run.backup_type, "status": run.status, "comment": run.comment},
        )
    messages.success(request, f"Backup #{run.pk} поставлен в очередь.")
    return redirect("core:system_status")


@login_required
@permission_required("core.run_backup", raise_exception=True)
def delete_backup(request, key):
    preview = get_backup_delete_preview(key)
    if not preview:
        messages.error(request, "Backup не найден.")
        return redirect("core:system_status")

    if request.method == "POST":
        if get_system_state().mode != SystemState.MODE_ADMIN_ONLY:
            messages.error(request, "Удаление backup доступно только в режиме профилактики.")
            return redirect("core:delete_backup", key=key)
        if request.POST.get("confirm_text", "").strip() != "УДАЛИТЬ":
            messages.error(request, "Для удаления нужно ввести подтверждение: УДАЛИТЬ.")
            return redirect("core:delete_backup", key=key)
        try:
            result = delete_backup_by_key(key, user=request.user, request=request)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("core:delete_backup", key=key)
        messages.success(
            request,
            (
                f"Backup удалён: файлов удалено {len(result['deleted_files'])}, "
                f"уже отсутствовало {len(result['missing_files'])}."
            ),
        )
        return redirect("core:system_status")

    return render(request, "core/backup_delete.html", {
        "preview": preview,
        "system_state": get_system_state(),
        "MODE_ADMIN_ONLY": SystemState.MODE_ADMIN_ONLY,
    })


@login_required
@permission_required("core.run_restore", raise_exception=True)
def start_restore(request):
    if request.method != "POST":
        return redirect("core:system_status")
    if get_system_state().mode != SystemState.MODE_ADMIN_ONLY:
        messages.error(request, "Restore из UI доступен только в режиме профилактики.")
        return redirect("core:system_status")
    if has_active_operation():
        messages.error(request, "Уже выполняется backup или restore.")
        return redirect("core:system_status")
    if request.POST.get("confirm_text", "").strip() != "ВОССТАНОВИТЬ":
        messages.error(request, "Для restore нужно ввести подтверждение: ВОССТАНОВИТЬ.")
        return redirect("core:system_status")

    full_entry = get_backup_entry_by_key(request.POST.get("full_backup", ""))
    incremental_key = request.POST.get("incremental_backup", "")
    incremental_entry = get_backup_entry_by_key(incremental_key) if incremental_key else None
    if not full_entry or full_entry["backup_type"] not in (BackupRun.TYPE_FULL, BackupRun.TYPE_PRE_RESTORE):
        messages.error(request, "Выберите корректный полный backup.")
        return redirect("core:system_status")
    if incremental_entry:
        if incremental_entry["backup_type"] != BackupRun.TYPE_INCREMENTAL:
            messages.error(request, "Выбран некорректный инкрементальный backup.")
            return redirect("core:system_status")
        if incremental_entry["baseline_manifest"] != full_entry["manifest_path"]:
            messages.error(request, "Инкрементальный backup не относится к выбранному полному backup.")
            return redirect("core:system_status")

    with transaction.atomic():
        state, _ = SystemState.objects.select_for_update().get_or_create(singleton_key=1)
        if state.mode != SystemState.MODE_ADMIN_ONLY:
            messages.error(request, "Restore из UI доступен только в режиме профилактики.")
            return redirect("core:system_status")
        if has_active_operation():
            messages.error(request, "Уже выполняется backup или restore.")
            return redirect("core:system_status")

        run = RestoreRun.objects.create(
            initiated_by=request.user,
            status=RestoreRun.STATUS_QUEUED,
            full_manifest_path=full_entry["manifest_path"],
            incremental_manifest_path=incremental_entry["manifest_path"] if incremental_entry else "",
            selected_manifest={
                "full": full_entry["manifest"],
                "incremental": incremental_entry["manifest"] if incremental_entry else None,
            },
        )
        write_audit_log(
            entity_type=AuditLog.ENTITY_RESTORE,
            entity_id=run.pk,
            action=AuditLog.ACTION_RESTORE_QUEUED,
            request=request,
            source=AuditLog.SOURCE_UI,
            new_values={
                "status": run.status,
                "full_manifest_path": run.full_manifest_path,
                "incremental_manifest_path": run.incremental_manifest_path,
            },
        )
    messages.success(request, f"Restore #{run.pk} поставлен в очередь.")
    return redirect("core:system_status")


@login_required
def field_settings(request):
    if request.method == "POST":
        _require_perm(request.user, "core.change_fieldsettings")
    else:
        _require_perm(request.user, "core.view_fieldsettings")

    entity = request.GET.get("entity", "auto_shipment")
    if entity not in ("auto_shipment", "rail_shipment"):
        entity = "auto_shipment"

    if request.method == "POST":
        entity = request.POST.get("entity", "auto_shipment")
        if entity not in ("auto_shipment", "rail_shipment"):
            entity = "auto_shipment"
        field_order = request.POST.get("field_order", "")
        order_map = {name: idx for idx, name in enumerate(field_order.split(",")) if name}

        fields = list(FieldSettings.objects.filter(entity=entity).order_by("sort_order", "field_name"))
        for fs in fields:
            if fs.field_name in order_map:
                fs.sort_order = order_map[fs.field_name]
            if not fs.is_system:
                fs.visible = f"visible_{fs.field_name}" in request.POST
                fs.required = f"required_{fs.field_name}" in request.POST
                fs.show_in_list = f"show_in_list_{fs.field_name}" in request.POST
                section_val = request.POST.get(f"section_{fs.field_name}", fs.section)
                if section_val in (FieldSettings.SECTION_MAIN, FieldSettings.SECTION_ADVANCED):
                    fs.section = section_val
            fs.allow_filter = f"allow_filter_{fs.field_name}" in request.POST
            fs.allow_sort = f"allow_sort_{fs.field_name}" in request.POST
            filter_type_val = request.POST.get(f"filter_type_{fs.field_name}", "none")
            if filter_type_val in ("none", "value", "date", "number", "text"):
                fs.filter_type = filter_type_val
            fs.sticky_col = f"sticky_col_{fs.field_name}" in request.POST
            preset_names = [p["name"] for p in ENTITY_PRESETS.get(entity, []) if p["name"] != "full"]
            memberships = [p for p in preset_names if f"preset_{p}_{fs.field_name}" in request.POST]
            fs.preset_membership = ",".join(memberships)

        sort_orders = [fs.sort_order for fs in fields]
        if len(sort_orders) != len(set(sort_orders)):
            messages.error(request, "Обнаружены дублирующиеся позиции порядка полей (sort_order). Сохранение отменено.")
            fields.sort(key=lambda item: (item.sort_order, item.field_name))
            return render(request, "core/field_settings.html", {
                "fields": fields,
                "entity": entity,
                "SECTION_MAIN": FieldSettings.SECTION_MAIN,
                "SECTION_ADVANCED": FieldSettings.SECTION_ADVANCED,
                "preset_defs": ENTITY_PRESETS.get(entity, []),
            })

        validation_errors = validate_field_settings(entity, fields)
        if validation_errors:
            for error in validation_errors[:8]:
                messages.error(request, error)
            if len(validation_errors) > 8:
                messages.error(request, f"Ещё ошибок: {len(validation_errors) - 8}.")
            fields.sort(key=lambda item: (item.sort_order, item.field_name))
            preset_defs = ENTITY_PRESETS.get(entity, [])
            return render(request, "core/field_settings.html", {
                "fields": fields,
                "entity": entity,
                "SECTION_MAIN": FieldSettings.SECTION_MAIN,
                "SECTION_ADVANCED": FieldSettings.SECTION_ADVANCED,
                "preset_defs": preset_defs,
            })

        with transaction.atomic():
            FieldSettings.objects.bulk_update(
                fields,
                [
                    "sort_order", "visible", "required", "show_in_list",
                    "section", "allow_filter", "allow_sort", "filter_type",
                    "sticky_col", "preset_membership",
                ],
            )
        invalidate_entity_config(entity)
        messages.success(request, "Настройки полей сохранены.")
        return redirect(f"/settings/fields/?entity={entity}")

    fields = FieldSettings.objects.filter(entity=entity).order_by("sort_order", "field_name")
    preset_defs = ENTITY_PRESETS.get(entity, [])
    return render(request, "core/field_settings.html", {
        "fields": fields,
        "entity": entity,
        "SECTION_MAIN": FieldSettings.SECTION_MAIN,
        "SECTION_ADVANCED": FieldSettings.SECTION_ADVANCED,
        "preset_defs": preset_defs,
    })
