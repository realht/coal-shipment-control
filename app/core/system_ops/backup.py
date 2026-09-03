import gzip
import json
import os
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditLog
from documents.models import ShipmentDocument

from core import system_ops as _ops

from ..models import BackupRun, BackupSchedule, RestoreRun
from ._shared import (
    MANIFEST_SUFFIX,
    _iso_now,
    _now_label,
    _runtime_build_identity,
    _write_json,
    get_backup_dir,
    get_media_root,
    logger,
)


def _backup_reference_consistency(current_inventory):
    """V18-MED-3: сверить file_path активных ShipmentDocument с инвентарём uploads.

    Standalone-backup (ночной scheduled / ручной) снимает dump БД до
    инвентаризации uploads и идёт без quiesce, поэтому конкурентная замена/удаление
    документа между dump и архивом может оставить в БД ссылку на файл, которого нет
    в срезе uploads. Возвращаем такие пути (в posix-форме ключей инвентаря) для
    записи в manifest — backup при этом не падает, это детекция/наблюдаемость.
    """
    inventory_keys = set(current_inventory)
    referenced = (
        ShipmentDocument.objects.filter(is_deleted=False, file_deleted_at__isnull=True)
        .exclude(file_path="")
        .values_list("file_path", flat=True)
    )
    missing = sorted(
        {
            Path(file_path).as_posix()
            for file_path in referenced
            if Path(file_path).as_posix() not in inventory_keys
        }
    )
    return {"missing_referenced": missing, "checked_at": _iso_now()}


def scan_backup_manifests():
    backup_dir = get_backup_dir()
    entries = {}

    for run in BackupRun.objects.filter(status=BackupRun.STATUS_SUCCESS).exclude(manifest_path=""):
        path = Path(run.manifest_path)
        if path.exists():
            manifest = run.manifest if isinstance(run.manifest, dict) and run.manifest else None
            if manifest is None:
                try:
                    manifest = _ops._load_json(path)
                except (OSError, json.JSONDecodeError):
                    continue
            entries[path.name] = _entry_from_manifest(path, manifest, run)

    if backup_dir.exists():
        for path in backup_dir.glob(f"*{MANIFEST_SUFFIX}"):
            if path.name in entries:
                continue
            try:
                manifest = _ops._load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            entries[path.name] = _entry_from_manifest(path, manifest, None)

    return sorted(entries.values(), key=lambda item: item.get("created_at", ""), reverse=True)


def _entry_from_manifest(path, manifest, run):
    uploads = manifest.get("uploads", {})
    database = manifest.get("database", {})
    return {
        "key": path.name,
        "manifest_path": str(path),
        "manifest": manifest,
        "backup_type": manifest.get("backup_type", ""),
        "created_at": manifest.get("created_at", ""),
        "db_path": database.get("path", ""),
        "uploads_path": uploads.get("archive", ""),
        "total_size": manifest.get("total_size", 0),
        "comment": manifest.get("comment", getattr(run, "comment", "")),
        "baseline_manifest": uploads.get("baseline_manifest", ""),
        "run": run,
    }


def get_backup_entry_by_key(key):
    for entry in scan_backup_manifests():
        if entry["key"] == key:
            return entry
    return None


def _safe_backup_file_entry(path_value, role):
    if not path_value:
        return None

    backup_dir = get_backup_dir().resolve()
    path = Path(path_value)
    try:
        resolved = path.resolve()
        resolved.relative_to(backup_dir)
    except (OSError, ValueError):
        return {
            "role": role,
            "path": str(path),
            "exists": path.exists(),
            "size": 0,
            "safe": False,
        }

    exists = resolved.exists()
    size = 0
    if exists and resolved.is_file():
        try:
            size = resolved.stat().st_size
        except OSError:
            size = 0
    return {
        "role": role,
        "path": str(resolved),
        "exists": exists,
        "size": size,
        "safe": True,
    }


def _backup_entry_files(entry):
    files = []
    seen = set()
    for role, path_value in (
        ("manifest", entry.get("manifest_path", "")),
        ("database", entry.get("db_path", "")),
        ("uploads", entry.get("uploads_path", "")),
    ):
        file_entry = _safe_backup_file_entry(path_value, role)
        if not file_entry or file_entry["path"] in seen:
            continue
        seen.add(file_entry["path"])
        files.append(file_entry)
    return files


def _active_restore_uses_manifest(manifest_paths):
    if not manifest_paths:
        return False
    return RestoreRun.objects.filter(
        status__in=[RestoreRun.STATUS_QUEUED, RestoreRun.STATUS_RUNNING]
    ).filter(
        Q(full_manifest_path__in=manifest_paths)
        | Q(incremental_manifest_path__in=manifest_paths)
    ).exists()


def get_backup_delete_preview(key):
    entry = get_backup_entry_by_key(key)
    if not entry:
        return None

    backup_entries = scan_backup_manifests()
    entries_to_delete = [entry]
    if entry["backup_type"] in (BackupRun.TYPE_FULL, BackupRun.TYPE_PRE_RESTORE):
        entries_to_delete.extend(
            item for item in backup_entries
            if item["backup_type"] == BackupRun.TYPE_INCREMENTAL
            and item.get("baseline_manifest") == entry["manifest_path"]
        )

    files = []
    seen_files = set()
    for item in entries_to_delete:
        item_files = _backup_entry_files(item)
        item["delete_files"] = item_files
        for file_entry in item_files:
            if file_entry["path"] in seen_files:
                continue
            seen_files.add(file_entry["path"])
            files.append(file_entry)

    blockers = []
    if entry["backup_type"] in (BackupRun.TYPE_FULL, BackupRun.TYPE_PRE_RESTORE):
        same_type_entries = [
            item for item in backup_entries
            if item["backup_type"] == entry["backup_type"]
        ]
        if same_type_entries and same_type_entries[0]["manifest_path"] == entry["manifest_path"]:
            blockers.append(
                f"Нельзя удалить последний успешный {entry['backup_type']} backup."
            )

    manifest_paths = [item["manifest_path"] for item in entries_to_delete if item.get("manifest_path")]
    if _active_restore_uses_manifest(manifest_paths):
        blockers.append("Backup участвует в активном или queued restore.")

    if any(not item["safe"] for item in files):
        blockers.append("Backup содержит путь вне BACKUP_DIR; удаление заблокировано.")

    return {
        "entry": entry,
        "entries_to_delete": entries_to_delete,
        "files": files,
        "blockers": blockers,
        "can_delete": not blockers,
    }


def delete_backup_by_key(key, user=None, request=None):
    preview = get_backup_delete_preview(key)
    if not preview:
        raise ValueError("Backup не найден.")
    if not preview["can_delete"]:
        raise ValueError(" ".join(preview["blockers"]))

    deleted_files = []
    missing_files = []
    for file_entry in preview["files"]:
        path = Path(file_entry["path"])
        if not path.exists():
            missing_files.append(file_entry["path"])
            continue
        path.unlink()
        deleted_files.append(file_entry["path"])

    _ops.write_audit_log(
        entity_type=AuditLog.ENTITY_BACKUP,
        entity_id=getattr(preview["entry"].get("run"), "pk", 0) or 0,
        action=AuditLog.ACTION_DELETE,
        request=request,
        user=user,
        source=AuditLog.SOURCE_UI,
        old_values={
            "selected_backup": {
                "key": preview["entry"]["key"],
                "backup_type": preview["entry"]["backup_type"],
                "manifest_path": preview["entry"]["manifest_path"],
            },
            "entries": [
                {
                    "key": item["key"],
                    "backup_type": item["backup_type"],
                    "manifest_path": item["manifest_path"],
                }
                for item in preview["entries_to_delete"]
            ],
        },
        new_values={
            "deleted_files": deleted_files,
            "missing_files": missing_files,
            "backup_unavailable_for_restore": True,
        },
    )
    return {
        "deleted_files": deleted_files,
        "missing_files": missing_files,
        "entries": preview["entries_to_delete"],
    }


def _latest_full_manifest():
    runs = (
        BackupRun.objects.filter(
            status=BackupRun.STATUS_SUCCESS,
            backup_type__in=[BackupRun.TYPE_FULL, BackupRun.TYPE_PRE_RESTORE],
        )
        .exclude(manifest_path="")
        .order_by("-created_at")
    )
    for run in runs:
        if Path(run.manifest_path).exists():
            return _backup_run_entry(run)
    return None


def _dump_database(path):
    db = settings.DATABASES["default"]
    engine = db.get("ENGINE", "")
    if engine.endswith("sqlite3"):
        connection.ensure_connection()
        with gzip.open(path, "wt", encoding="utf-8") as dst:
            for line in connection.connection.iterdump():
                dst.write(f"{line}\n")
        return {
            "engine": "sqlite3",
            "path": str(path),
            "size": Path(path).stat().st_size,
            "sha256": _ops._sha256_file(path),
        }

    cmd = [
        getattr(settings, "BACKUP_MYSQLDUMP_BIN", "mysqldump"),
        "--skip-ssl",
        f"--user={db['USER']}",
        f"--host={db['HOST']}",
        f"--port={db.get('PORT', 3306)}",
        "--single-transaction",
        "--skip-add-locks",
        "--routines",
        "--triggers",
        db["NAME"],
    ]
    proc_env = {**os.environ}
    if db.get("PASSWORD"):
        proc_env["MYSQL_PWD"] = db["PASSWORD"]

    stderr_chunks = []
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=proc_env)

    def read_stderr():
        stderr_chunks.append(process.stderr.read())

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()
    try:
        with gzip.open(path, "wb") as stdout:
            shutil.copyfileobj(process.stdout, stdout, length=1024 * 1024)
    except Exception:
        process.kill()
        process.wait()
        stderr_thread.join()
        raise
    finally:
        process.stdout.close()

    returncode = process.wait()
    stderr_thread.join()
    if returncode != 0:
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise RuntimeError(stderr or "mysqldump failed")
    return {
        "engine": "mysql",
        "path": str(path),
        "size": Path(path).stat().st_size,
        "sha256": _ops._sha256_file(path),
    }


def _create_uploads_archive(path, included_files):
    root = get_media_root()
    missing = []
    with tarfile.open(path, "w:gz") as tar:
        for rel in included_files:
            source = root / rel
            if source.exists() and source.is_file():
                tar.add(source, arcname=rel)
            else:
                # V17-MED-3: файл исчез между inventory и архивацией —
                # исключаем из архива (и, по возврату, из манифеста) вместо abort.
                # Принцип V15-H1 соблюдён: архив содержит ровно то, что попадёт
                # в manifest.uploads.files/included_files — без рассогласования.
                missing.append(rel)
    return {"size": Path(path).stat().st_size, "sha256": _ops._sha256_file(path), "missing": missing}


def _backup_audit_source(run):
    if run.source == BackupRun.SOURCE_SCHEDULER:
        return AuditLog.SOURCE_SCHEDULER
    if run.source == BackupRun.SOURCE_SCRIPT:
        return AuditLog.SOURCE_SCRIPT
    if run.source == BackupRun.SOURCE_RESTORE:
        return AuditLog.SOURCE_RESTORE
    return AuditLog.SOURCE_UI


def _audit_backup_run(run, action, error_message=""):
    identity = _runtime_build_identity()
    payload = {
        "backup_type": run.backup_type,
        "status": run.status,
        "comment": run.comment,
        "manifest_path": run.manifest_path,
        "app_version": identity["app_version"],
        "app_build_id": identity["app_build_id"],
    }
    if error_message:
        payload["error_message"] = error_message
    _ops._write_system_audit(
        entity_type=AuditLog.ENTITY_BACKUP,
        entity_id=run.pk,
        action=action,
        user=run.initiated_by,
        source=_backup_audit_source(run),
        new_values=payload,
    )


def _touch_backup_schedule(run):
    if not run.schedule_id:
        return
    BackupSchedule.objects.filter(pk=run.schedule_id).update(
        last_run=run,
        last_run_at=run.finished_at,
    )


def create_backup(backup_type, initiated_by=None, run=None, comment="", source=None):
    if backup_type not in {BackupRun.TYPE_FULL, BackupRun.TYPE_INCREMENTAL, BackupRun.TYPE_PRE_RESTORE}:
        raise ValueError("Unsupported backup type")

    backup_dir = get_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    run = run or BackupRun.objects.create(
        backup_type=backup_type,
        initiated_by=initiated_by,
        source=source or BackupRun.SOURCE_UI,
    )
    run.status = BackupRun.STATUS_RUNNING
    run.started_at = timezone.now()
    run.error_message = ""
    if source:
        run.source = source
    if comment:
        run.comment = comment.strip()[:500]
    run.save(update_fields=["status", "started_at", "error_message", "comment", "source"])
    _audit_backup_run(run, AuditLog.ACTION_BACKUP_STARTED)

    db_path = uploads_path = manifest_path = None
    try:
        requested_type = backup_type
        baseline = _latest_full_manifest() if backup_type == BackupRun.TYPE_INCREMENTAL else None
        if backup_type == BackupRun.TYPE_INCREMENTAL and baseline is None:
            backup_type = BackupRun.TYPE_FULL
            run.backup_type = BackupRun.TYPE_FULL
            run.save(update_fields=["backup_type"])

        label = _now_label()
        db_path = backup_dir / f"db_{backup_type}_{label}.sql.gz"
        uploads_path = backup_dir / f"uploads_{backup_type}_{label}.tar.gz"
        manifest_path = backup_dir / f"backup_{backup_type}_{label}{MANIFEST_SUFFIX}"

        db_info = _ops._dump_database(db_path)

        if backup_type == BackupRun.TYPE_INCREMENTAL:
            prev_inventory = baseline["manifest"].get("uploads", {}).get("files", {})
        else:
            prev_full = _latest_full_manifest()
            prev_inventory = prev_full["manifest"].get("uploads", {}).get("files", {}) if prev_full else {}

        current_inventory = _ops._uploads_inventory(previous=prev_inventory)

        if backup_type == BackupRun.TYPE_INCREMENTAL:
            baseline_inventory = baseline["manifest"].get("uploads", {}).get("files", {})
            included = [
                rel for rel, meta in current_inventory.items()
                if baseline_inventory.get(rel) != meta
            ]
            deleted = sorted(set(baseline_inventory) - set(current_inventory))
            mode = "incremental"
            baseline_manifest = baseline["manifest_path"]
        else:
            included = sorted(current_inventory)
            deleted = []
            mode = "full"
            baseline_manifest = ""

        uploads_archive_info = _ops._create_uploads_archive(uploads_path, included)
        missing = uploads_archive_info.get("missing", [])
        if missing:
            # V17-MED-3: держим manifest (files + included_files) согласованным с
            # содержимым архива — исчезнувшие файлы убираем из обоих списков.
            logger.warning(
                "Backup %s: %d upload(s) vanished during archiving, excluded: %r",
                run.pk, len(missing), missing[:5],
            )
            missing_set = set(missing)
            included = [rel for rel in included if rel not in missing_set]
            for rel in missing:
                current_inventory.pop(rel, None)
        total_size = db_info["size"] + uploads_archive_info["size"]
        consistency = _backup_reference_consistency(current_inventory)
        if consistency["missing_referenced"]:
            logger.warning(
                "Backup %s: %d document reference(s) missing from uploads snapshot: %r",
                run.pk, len(consistency["missing_referenced"]), consistency["missing_referenced"][:5],
            )
        identity = _runtime_build_identity()
        manifest = {
            "version": 2,
            **identity,
            "backup_type": backup_type,
            "requested_type": requested_type,
            "created_at": _iso_now(),
            "comment": run.comment,
            "database": db_info,
            "uploads": {
                "mode": mode,
                "archive": str(uploads_path),
                "size": uploads_archive_info["size"],
                "sha256": uploads_archive_info["sha256"],
                "files": current_inventory,
                "consistency": consistency,
                "included_files": sorted(included),
                "deleted_files": deleted,
                "baseline_manifest": baseline_manifest,
            },
            "total_size": total_size,
        }
        _write_json(manifest_path, manifest)

        run.status = BackupRun.STATUS_SUCCESS
        run.finished_at = timezone.now()
        run.db_path = str(db_path)
        run.uploads_path = str(uploads_path)
        run.manifest_path = str(manifest_path)
        run.total_size = total_size
        run.manifest = manifest
        run.save()
        _touch_backup_schedule(run)
        _audit_backup_run(run, AuditLog.ACTION_BACKUP_SUCCESS)
        return run
    except Exception as exc:
        run.status = BackupRun.STATUS_ERROR
        run.finished_at = timezone.now()
        run.error_message = str(exc)
        run.save(update_fields=["status", "finished_at", "error_message"])
        _touch_backup_schedule(run)
        _audit_backup_run(run, AuditLog.ACTION_BACKUP_ERROR, str(exc))
        for _p in (db_path, uploads_path, manifest_path):
            if _p is not None:
                Path(_p).unlink(missing_ok=True)
        raise


def _backup_run_entry(run):
    manifest_path = Path(run.manifest_path) if run.manifest_path else None
    manifest = run.manifest or {}
    if manifest_path and manifest_path.exists():
        try:
            manifest = _ops._load_json(manifest_path)
        except (OSError, json.JSONDecodeError):
            manifest = run.manifest or {}
    path = manifest_path or Path("")
    entry = _entry_from_manifest(path, manifest, run)
    entry["created_at_dt"] = run.created_at
    entry["backup_type"] = run.backup_type
    entry["manifest_path"] = run.manifest_path
    entry["db_path"] = entry.get("db_path") or run.db_path
    entry["uploads_path"] = entry.get("uploads_path") or run.uploads_path
    return entry
