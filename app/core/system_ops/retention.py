import json
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from documents.models import ShipmentDocument

from core import system_ops as _ops

from ..models import BackupRun
from ._shared import MANIFEST_SUFFIX, get_backup_dir, get_media_root, logger


def _parse_manifest_created_at(value):
    """Разобрать manifest `created_at` (ISO-строку) в aware datetime или None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_default_timezone())
    return parsed


def _orphan_manifest_entries(known_paths):
    """V17-LOW-2: манифесты на диске без BackupRun (осиротевшие после restore)."""
    backup_dir = get_backup_dir()
    if not backup_dir.exists():
        return []
    entries = []
    for path in sorted(backup_dir.glob(f"*{MANIFEST_SUFFIX}")):
        if str(path) in known_paths:
            continue
        try:
            manifest = _ops._load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        entry = _ops._entry_from_manifest(path, manifest, None)
        entry["created_at_dt"] = _parse_manifest_created_at(manifest.get("created_at"))
        entries.append(entry)
    return entries


def _apply_retention(now=None):
    now = now or timezone.now()
    entries = [
        _ops._backup_run_entry(run)
        for run in BackupRun.objects.filter(status=BackupRun.STATUS_SUCCESS)
        .exclude(manifest_path="")
        .order_by("-created_at")
    ]
    known_paths = {entry["manifest_path"] for entry in entries if entry.get("manifest_path")}
    entries.extend(_orphan_manifest_entries(known_paths))
    if not entries:
        return {"deleted_entries": 0, "deleted_files": 0}
    # Пересортировать объединённый список, чтобы «новейший full/pre-restore»
    # (newest_protected ниже) корректно защищал в т.ч. дисковый орфан.
    entries.sort(
        key=lambda entry: (entry.get("created_at_dt") is not None, entry.get("created_at_dt")),
        reverse=True,
    )

    full_cutoff = now - timedelta(days=getattr(settings, "BACKUP_FULL_KEEP_DAYS", 30))
    incr_cutoff = now - timedelta(days=getattr(settings, "BACKUP_INCREMENTAL_KEEP_DAYS", 14))
    pre_restore_cutoff = now - timedelta(
        days=getattr(settings, "BACKUP_PRE_RESTORE_KEEP_DAYS", 30)
    )

    incrementals_by_baseline = {}
    for entry in entries:
        if entry["backup_type"] == BackupRun.TYPE_INCREMENTAL:
            baseline = entry.get("baseline_manifest", "")
            if baseline:
                incrementals_by_baseline.setdefault(baseline, []).append(entry)

    newest_protected = {}
    for backup_type in (BackupRun.TYPE_FULL, BackupRun.TYPE_PRE_RESTORE):
        typed = [entry for entry in entries if entry["backup_type"] == backup_type]
        if typed:
            newest_protected[backup_type] = typed[0]["manifest_path"]

    cutoff_by_type = {
        BackupRun.TYPE_FULL: full_cutoff,
        BackupRun.TYPE_PRE_RESTORE: pre_restore_cutoff,
    }
    to_delete = []
    seen = set()

    for backup_type, cutoff in cutoff_by_type.items():
        for entry in [item for item in entries if item["backup_type"] == backup_type]:
            mp = entry.get("manifest_path")
            if not mp or mp == newest_protected.get(backup_type):
                continue
            if entry["created_at_dt"] is None or entry["created_at_dt"] > cutoff:
                continue
            dependents = incrementals_by_baseline.get(mp, [])
            if any(
                dependent.get("created_at_dt") is not None
                and dependent["created_at_dt"] > incr_cutoff
                for dependent in dependents
            ):
                continue
            manifest_paths = [mp] + [
                dependent["manifest_path"] for dependent in dependents if dependent.get("manifest_path")
            ]
            if _ops._active_restore_uses_manifest([path for path in manifest_paths if path]):
                continue
            for item in [entry] + dependents:
                item_mp = item.get("manifest_path")
                if item_mp and item_mp not in seen:
                    seen.add(item_mp)
                    to_delete.append(item)

    for entry in entries:
        if entry["backup_type"] != BackupRun.TYPE_INCREMENTAL:
            continue
        mp = entry.get("manifest_path")
        if not mp or mp in seen:
            continue
        if entry["created_at_dt"] is None or entry["created_at_dt"] > incr_cutoff:
            continue
        baseline = entry.get("baseline_manifest", "")
        check_paths = [path for path in [mp, baseline] if path]
        if _ops._active_restore_uses_manifest(check_paths):
            continue
        seen.add(mp)
        to_delete.append(entry)

    deleted_files = 0
    for entry in to_delete:
        for file_entry in _ops._backup_entry_files(entry):
            if not file_entry.get("safe"):
                continue
            path = Path(file_entry["path"])
            try:
                if path.exists():
                    path.unlink()
                    deleted_files += 1
            except OSError:
                continue
    return {"deleted_entries": len(to_delete), "deleted_files": deleted_files}


def _safe_media_file_path(path_value):
    if not path_value:
        return None
    media_root = get_media_root().resolve()
    path = Path(path_value)
    if not path.is_absolute():
        path = media_root / path
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(media_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved


def _cleanup_deleted_document_files(now):
    keep_days = getattr(settings, "DELETED_DOCUMENT_FILE_KEEP_DAYS", 30)
    cutoff = now - timedelta(days=keep_days)
    candidates = ShipmentDocument.objects.filter(
        is_deleted=True,
        deleted_at__isnull=False,
        deleted_at__lt=cutoff,
        file_deleted_at__isnull=True,
    )
    deleted = 0
    missing = 0
    unsafe = 0
    for doc in candidates.iterator():
        path = _safe_media_file_path(doc.file_path)
        if path is None:
            unsafe += 1
            continue
        try:
            if path.exists():
                path.unlink()
                deleted += 1
            else:
                missing += 1
            doc.file_deleted_at = now
            doc.save(update_fields=["file_deleted_at"])
        except OSError:
            logger.warning("Failed to delete soft-deleted document file: %s", path)
    legacy_skipped = ShipmentDocument.objects.filter(
        is_deleted=True,
        deleted_at__isnull=True,
        file_deleted_at__isnull=True,
    ).count()
    return {
        "document_files_deleted": deleted,
        "document_files_missing": missing,
        "document_files_unsafe": unsafe,
        "legacy_deleted_documents_skipped": legacy_skipped,
    }
