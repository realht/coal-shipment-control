import gzip
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
from contextlib import closing
from pathlib import Path

from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.utils import timezone

from audit.models import AuditLog

from core import system_ops as _ops

from ..models import BackupRun, RestoreRun, SystemState
from ._shared import (
    _safe_user_pk,
    _scheduler_heartbeat_paused,
    get_backup_dir,
    get_media_root,
)


def _open_sql_dump(path):
    with Path(path).open("rb") as fh:
        prefix = fh.read(2)
    if prefix == b"\x1f\x8b":
        return gzip.open(path, "rb")
    return Path(path).open("rb")


def _mysql_drop_all_preamble(table_names):
    """SQL that drops every current table, so an older dump does not leave
    stray tables behind (which would break a later migrate). Mirrors the clean
    slate the sqlite branch gets by rewriting the whole database file."""
    if not table_names:
        return b""
    quoted = ", ".join("`" + name.replace("`", "``") + "`" for name in table_names)
    sql = (
        "SET FOREIGN_KEY_CHECKS=0;\n"
        f"DROP TABLE IF EXISTS {quoted};\n"
        "SET FOREIGN_KEY_CHECKS=1;\n"
    )
    return sql.encode("utf-8")


def _restore_database(path, engine):
    db = settings.DATABASES["default"]
    manifest_vendor = "sqlite" if engine == "sqlite3" else "mysql"
    if manifest_vendor != connection.vendor:
        raise RuntimeError(
            f"Backup engine '{engine}' does not match database vendor "
            f"'{connection.vendor}'; refusing to restore."
        )

    if connection.vendor == "sqlite":
        connection.close()
        _ops._restore_sqlite_database(path, db["NAME"])
        return

    # Snapshot the current schema before closing the connection so we can drop
    # it ahead of loading the dump (V17-MED-1).
    table_names = connection.introspection.table_names()
    connection.close()

    cmd = [
        getattr(settings, "BACKUP_MYSQL_BIN", "mysql"),
        "--skip-ssl",
        f"--user={db['USER']}",
        f"--host={db['HOST']}",
        f"--port={db.get('PORT', 3306)}",
        db["NAME"],
    ]
    proc_env = {**os.environ}
    if db.get("PASSWORD"):
        proc_env["MYSQL_PWD"] = db["PASSWORD"]

    drop_preamble = _mysql_drop_all_preamble(table_names)

    stderr_chunks = []
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=proc_env)

    def read_stderr():
        stderr_chunks.append(process.stderr.read())

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()
    # Keep the heartbeat thread off the SystemState singleton while the dump is
    # loaded, otherwise its update_or_create can insert a row that collides with
    # the dump's own INSERT and aborts the restore (V17-MED-4).
    _scheduler_heartbeat_paused.set()
    try:
        try:
            if drop_preamble:
                process.stdin.write(drop_preamble)
            with _open_sql_dump(path) as stdin:
                shutil.copyfileobj(stdin, process.stdin, length=1024 * 1024)
            process.stdin.close()
        except Exception:
            process.kill()
            process.wait()
            stderr_thread.join()
            raise

        returncode = process.wait()
        stderr_thread.join()
    finally:
        _scheduler_heartbeat_paused.clear()

    if returncode != 0:
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        raise RuntimeError(stderr or "mysql restore failed")


def _restore_sqlite_database(dump_path, database_path):
    target_path = Path(database_path)
    if str(target_path) == ":memory:":
        raise RuntimeError("SQLite in-memory database cannot be restored from backup")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f".{target_path.name}.restore-tmp")
    if temp_path.exists():
        temp_path.unlink()

    try:
        with gzip.open(dump_path, "rt", encoding="utf-8") as source:
            script = source.read()
        with closing(sqlite3.connect(temp_path)) as sqlite_connection:
            sqlite_connection.executescript(script)
            sqlite_connection.commit()
        temp_path.replace(target_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _safe_extract_tar(path, target_root):
    target_root = Path(target_root).resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "r:gz") as tar:
        for member in tar:
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(f"Unsafe tar member type in archive: {member.name}")

            member_path = (target_root / member.name).resolve()
            try:
                member_path.relative_to(target_root)
            except ValueError as exc:
                raise RuntimeError(f"Unsafe path in archive: {member.name}") from exc

            if member.isdir():
                member_path.mkdir(parents=True, exist_ok=True)
                continue

            source = tar.extractfile(member)
            if source is None:
                raise RuntimeError(f"Cannot extract archive member: {member.name}")
            member_path.parent.mkdir(parents=True, exist_ok=True)
            with source, member_path.open("wb") as target:
                shutil.copyfileobj(source, target)


def _clear_media_root():
    root = get_media_root()
    root.mkdir(parents=True, exist_ok=True)
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _extract_uploads_to_staging(full_manifest, incremental_manifest=None):
    """Extract uploads to a temp staging dir before touching the DB.

    Staging lives *inside* MEDIA_ROOT so that the later swap of extracted
    files into place is a same-device rename instead of a cross-device copy.
    In prod MEDIA_ROOT is a bind-mount point; its parent (/app) and the system
    tempdir are on the container overlay filesystem — different devices.
    """
    media_root = get_media_root()
    media_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore_staging_", dir=str(media_root)))
    try:
        _ops._safe_extract_tar(full_manifest["uploads"]["archive"], staging)
        if incremental_manifest:
            _ops._safe_extract_tar(incremental_manifest["uploads"]["archive"], staging)
            for rel in incremental_manifest["uploads"].get("deleted_files", []):
                target = (staging / rel).resolve()
                try:
                    target.relative_to(staging.resolve())
                except ValueError:
                    continue
                if target.exists():
                    target.unlink()
        return staging
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _is_restore_internal_dir(name):
    """Internal scratch dirs created inside MEDIA_ROOT during restore."""
    return name.startswith(".restore_staging_") or name.startswith(".restore_old.")


def _restore_old_holder_path(media_root):
    base = media_root / f".restore_old.{os.getpid()}"
    if not base.exists():
        return base
    counter = 1
    while True:
        candidate = media_root / f"{base.name}.{counter}"
        if not candidate.exists():
            return candidate
        counter += 1


def _swap_staging_to_media(staging):
    """Swap staging content into MEDIA_ROOT, operating on *contents* only.

    The MEDIA_ROOT directory itself is never moved/removed: in prod it is a
    bind-mount point, so renaming or rmdir'ing it fails (EBUSY) and a
    ``shutil.move`` fallback would wipe the volume with no rollback. Instead we
    stash the current live content into a holder dir (same device), move the
    new content in, and either drop the holder on success or restore it on
    failure. Staging is inside MEDIA_ROOT, so every move is a cheap rename.
    """
    staging = Path(staging)
    media_root = get_media_root()
    media_root.mkdir(parents=True, exist_ok=True)

    old_holder = _restore_old_holder_path(media_root)
    old_holder.mkdir()

    moved_to_old = []  # names stashed from MEDIA_ROOT into old_holder
    moved_in = []      # names moved from staging into MEDIA_ROOT
    try:
        for child in list(media_root.iterdir()):
            if child == staging or child == old_holder or _is_restore_internal_dir(child.name):
                continue
            shutil.move(str(child), str(old_holder / child.name))
            moved_to_old.append(child.name)

        for child in list(staging.iterdir()):
            shutil.move(str(child), str(media_root / child.name))
            moved_in.append(child.name)
    except Exception:
        for name in moved_in:
            placed = media_root / name
            if placed.is_dir() and not placed.is_symlink():
                shutil.rmtree(placed, ignore_errors=True)
            elif placed.exists() or placed.is_symlink():
                placed.unlink()
        for name in moved_to_old:
            shutil.move(str(old_holder / name), str(media_root / name))
        shutil.rmtree(old_holder, ignore_errors=True)
        raise

    shutil.rmtree(old_holder, ignore_errors=True)


def _backup_run_defaults(run, user_pk):
    return {
        "backup_type": run.backup_type,
        "status": run.status,
        "initiated_by_id": user_pk,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "db_path": run.db_path,
        "uploads_path": run.uploads_path,
        "manifest_path": run.manifest_path,
        "total_size": run.total_size,
        "comment": run.comment,
        "manifest": run.manifest,
        "error_message": run.error_message,
    }


def _finalize_restore_run(restore_run, status, error_message="", pre_restore=None):
    user_pk = _safe_user_pk(restore_run.initiated_by)
    pre_restore_id = None
    if status == RestoreRun.STATUS_SUCCESS:
        _ops._mark_stale_active_operations(
            exclude_backup_id=getattr(pre_restore, "pk", None),
            exclude_restore_id=restore_run.pk,
        )
    if pre_restore is not None:
        BackupRun.objects.update_or_create(
            pk=pre_restore.pk,
            defaults=_backup_run_defaults(pre_restore, user_pk),
        )
        pre_restore_id = pre_restore.pk

    defaults = {
        "status": status,
        "initiated_by_id": user_pk,
        "started_at": restore_run.started_at,
        "finished_at": timezone.now(),
        "full_manifest_path": restore_run.full_manifest_path,
        "incremental_manifest_path": restore_run.incremental_manifest_path,
        "selected_manifest": restore_run.selected_manifest,
        "pre_restore_backup_id": pre_restore_id,
        "error_message": error_message,
    }
    restored_run, _ = RestoreRun.objects.update_or_create(pk=restore_run.pk, defaults=defaults)
    return restored_run


def _audit_restore_run(restore_run, action, error_message=""):
    payload = {
        "status": restore_run.status,
        "full_manifest_path": restore_run.full_manifest_path,
        "incremental_manifest_path": restore_run.incremental_manifest_path,
    }
    if error_message:
        payload["error_message"] = error_message
    _ops._write_system_audit(
        entity_type=AuditLog.ENTITY_RESTORE,
        entity_id=restore_run.pk,
        action=action,
        user=restore_run.initiated_by,
        source=AuditLog.SOURCE_RESTORE,
        new_values=payload,
    )


_REQUIRED_MANIFEST_SECTIONS = {
    "database": ["path"],
    "uploads": ["archive"],
}


def _validate_manifest(manifest):
    for section, keys in _REQUIRED_MANIFEST_SECTIONS.items():
        if not isinstance(manifest.get(section), dict):
            raise RuntimeError(f"Manifest missing section: {section!r}")
        for key in keys:
            if key not in manifest[section]:
                raise RuntimeError(f"Manifest missing key: {section!r}.{key!r}")


def _assert_within_backup_dir(path):
    try:
        Path(path).resolve().relative_to(get_backup_dir().resolve())
    except ValueError:
        raise RuntimeError(f"Manifest path is outside backup directory: {path}")


def _verify_database_dump(manifest, label):
    db = manifest.get("database", {})
    path = Path(db.get("path", ""))
    expected_size = db.get("size")
    if expected_size is not None:
        try:
            actual_size = path.stat().st_size
        except OSError:
            raise RuntimeError(f"{label}: database dump file is missing: {path}")
        if actual_size != expected_size:
            raise RuntimeError(
                f"{label}: database dump size mismatch: expected {expected_size}, "
                f"got {actual_size} ({path})"
            )
    expected_sha256 = db.get("sha256")
    if expected_sha256 and _ops._sha256_file(path) != expected_sha256:
        raise RuntimeError(f"{label}: database dump checksum mismatch ({path})")


def _verify_uploads_archive_file(manifest, label):
    uploads = manifest.get("uploads", {})
    path = Path(uploads.get("archive", ""))
    expected_size = uploads.get("size")
    if expected_size is not None:
        try:
            actual_size = path.stat().st_size
        except OSError:
            raise RuntimeError(f"{label}: uploads archive file is missing: {path}")
        if actual_size != expected_size:
            raise RuntimeError(f"{label}: uploads archive size mismatch ({path})")
    expected_sha256 = uploads.get("sha256")
    if expected_sha256 and _ops._sha256_file(path) != expected_sha256:
        raise RuntimeError(f"{label}: uploads archive checksum mismatch ({path})")


def _verify_uploads_contents(expected_files, actual_files):
    expected_keys = set(expected_files)
    actual_keys = set(actual_files)
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(actual_keys - expected_keys)
    mismatched = sorted(
        rel for rel in expected_keys & actual_keys
        if expected_files[rel].get("size") != actual_files[rel].get("size")
        or expected_files[rel].get("sha256") != actual_files[rel].get("sha256")
    )
    if missing or extra or mismatched:
        parts = []
        if missing:
            parts.append(f"missing {len(missing)} file(s) e.g. {missing[:5]!r}")
        if extra:
            parts.append(f"extra {len(extra)} file(s) e.g. {extra[:5]!r}")
        if mismatched:
            parts.append(f"checksum mismatch on {len(mismatched)} file(s) e.g. {mismatched[:5]!r}")
        raise RuntimeError("Restored uploads do not match backup manifest: " + "; ".join(parts))


def _reassert_restore_running_after_database_restore(user=None):
    connection.close()
    user_pk = _safe_user_pk(user)
    defaults = {
        "mode": SystemState.MODE_RESTORE_RUNNING,
        "reason": "Restore is running",
        "changed_by_id": user_pk,
    }
    for attempt in range(2):
        try:
            with transaction.atomic():
                state, _ = SystemState.objects.update_or_create(
                    singleton_key=1,
                    defaults=defaults,
                )
            return state
        except OperationalError:
            connection.close()
            if attempt == 1:
                raise
    raise RuntimeError("Could not reassert restore mode after database restore")


def _run_post_restore_commands():
    _ops.call_command("migrate", "--noinput")
    _ops.call_command("seed_groups")
    _ops.call_command("seed_field_config")


def restore_backup(restore_run):
    restore_run.status = RestoreRun.STATUS_RUNNING
    restore_run.started_at = timezone.now()
    restore_run.error_message = ""
    restore_run.save(update_fields=["status", "started_at", "error_message"])
    _audit_restore_run(restore_run, AuditLog.ACTION_RESTORE_STARTED)
    _ops.set_system_mode(
        SystemState.MODE_RESTORE_RUNNING,
        restore_run.initiated_by,
        "Restore is running",
        source=AuditLog.SOURCE_RESTORE,
    )
    pre_restore = None

    try:
        full_manifest = _ops._load_json(restore_run.full_manifest_path)
        incremental_manifest = (
            _ops._load_json(restore_run.incremental_manifest_path)
            if restore_run.incremental_manifest_path else None
        )

        _validate_manifest(full_manifest)
        if incremental_manifest:
            _validate_manifest(incremental_manifest)
        _assert_within_backup_dir(full_manifest["database"]["path"])
        _assert_within_backup_dir(full_manifest["uploads"]["archive"])
        if incremental_manifest:
            _assert_within_backup_dir(incremental_manifest["database"]["path"])
            _assert_within_backup_dir(incremental_manifest["uploads"]["archive"])

        _ops._restore_version_preflight(restore_run, full_manifest, incremental_manifest)

        pre_restore = BackupRun.objects.create(
            backup_type=BackupRun.TYPE_PRE_RESTORE,
            initiated_by=restore_run.initiated_by,
            source=BackupRun.SOURCE_RESTORE,
        )
        _ops.create_backup(BackupRun.TYPE_PRE_RESTORE, initiated_by=restore_run.initiated_by, run=pre_restore)
        if pre_restore.status != BackupRun.STATUS_SUCCESS:
            raise RuntimeError("Pre-restore backup was not created")
        restore_run.pre_restore_backup = pre_restore
        restore_run.save(update_fields=["pre_restore_backup"])

        # Cheap artifact verification (size/sha256 against manifest) before
        # spending time extracting a possibly large uploads archive.
        _verify_database_dump(full_manifest, "full backup")
        _verify_uploads_archive_file(full_manifest, "full backup")
        if incremental_manifest:
            _verify_database_dump(incremental_manifest, "incremental backup")
            _verify_uploads_archive_file(incremental_manifest, "incremental backup")

        # Extract uploads to staging BEFORE touching the DB.
        # If tar extraction fails here, the DB is not yet changed.
        staging = _ops._extract_uploads_to_staging(full_manifest, incremental_manifest)
        try:
            expected_files = (incremental_manifest or full_manifest)["uploads"].get("files", {})
            if expected_files:
                actual_files = _ops._uploads_inventory(root=staging)
                _verify_uploads_contents(expected_files, actual_files)

            db_manifest = incremental_manifest or full_manifest
            db_info = db_manifest["database"]
            _ops._restore_database(db_info["path"], db_info.get("engine", "mysql"))
            _reassert_restore_running_after_database_restore(restore_run.initiated_by)
            _run_post_restore_commands()

            _ops._swap_staging_to_media(staging)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        restore_run = _finalize_restore_run(restore_run, RestoreRun.STATUS_SUCCESS, pre_restore=pre_restore)
        _audit_restore_run(restore_run, AuditLog.ACTION_RESTORE_SUCCESS)
        _ops.set_system_mode(
            SystemState.MODE_ADMIN_ONLY,
            restore_run.initiated_by,
            "Restore completed; admin verification required",
            source=AuditLog.SOURCE_RESTORE,
        )
        return restore_run
    except Exception as exc:
        restore_run = _finalize_restore_run(
            restore_run,
            RestoreRun.STATUS_ERROR,
            error_message=str(exc),
            pre_restore=pre_restore,
        )
        _audit_restore_run(restore_run, AuditLog.ACTION_RESTORE_ERROR, str(exc))
        _ops.set_system_mode(
            SystemState.MODE_ADMIN_ONLY,
            restore_run.initiated_by,
            f"Restore failed: {exc}",
            source=AuditLog.SOURCE_RESTORE,
        )
        raise
