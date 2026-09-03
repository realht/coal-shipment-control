"""Пакет-фасад core.system_ops.

Разбит на сервисные сабмодули (V20-TECH-1, DEC-055), но остаётся единым
namespace `core.system_ops`: каждое имя контракта — атрибут пакета. Сабмодули
маршрутизируют патчируемые (B-)функции через `from core import system_ops as _ops`
и зовут `_ops.<name>()`, иначе `patch("core.system_ops.<name>")` не подействует.

Ниже связываются singleton-объекты, которые тесты патчат как
`core.system_ops.shutil.*` / `.tempfile.*` / `.connection.*`, и имена
`call_command` / `write_audit_log` для реэкспорта/маршрутизации.
"""
import shutil
import tempfile

from django.core.management import call_command
from django.db import connection

from audit.services import write_audit_log

from ._shared import (
    MANIFEST_SUFFIX,
    _iso_now,
    _load_json,
    _now_label,
    _runtime_build_identity,
    _safe_user_pk,
    _scheduler_heartbeat_paused,
    _sha256_file,
    _uploads_inventory,
    _write_json,
    get_backup_dir,
    get_dir_size,
    get_media_root,
    logger,
)
from .state import (  # noqa: E402
    _touch_scheduler_heartbeat,
    _write_system_audit,
    can_view_system_status,
    get_system_state,
    get_system_state_readonly,
    recalculate_uploads_size,
    set_system_mode,
)
from .readiness import (  # noqa: E402
    _dir_writable,
    database_health,
    get_readiness_status,
)
from .recovery import (  # noqa: E402
    _scheduler_heartbeat_is_fresh,
    _stale_running_filter,
    recover_interrupted_restore,
    recover_stale_running_operations_on_scheduler_start,
)
from .version_preflight import (  # noqa: E402
    _audit_restore_version_decision,
    _compare_semver,
    _parse_semver,
    _restore_identity_payload,
    _restore_version_preflight,
    _SEMVER_RE,
)
from .queue import (  # noqa: E402
    _claim_next_queued_operation,
    _enqueue_due_scheduled_backup,
    _mark_stale_active_operations,
    _schedule_due,
    claim_scheduler_operation,
    get_active_operations,
    has_active_operation,
)
from .retention import (  # noqa: E402
    _apply_retention,
    _cleanup_deleted_document_files,
    _orphan_manifest_entries,
    _parse_manifest_created_at,
    _safe_media_file_path,
)
from .backup import (  # noqa: E402
    _active_restore_uses_manifest,
    _audit_backup_run,
    _backup_audit_source,
    _backup_entry_files,
    _backup_reference_consistency,
    _backup_run_entry,
    _create_uploads_archive,
    _dump_database,
    _entry_from_manifest,
    _latest_full_manifest,
    _safe_backup_file_entry,
    _touch_backup_schedule,
    create_backup,
    delete_backup_by_key,
    get_backup_delete_preview,
    get_backup_entry_by_key,
    scan_backup_manifests,
)
from .restore import (  # noqa: E402
    _assert_within_backup_dir,
    _audit_restore_run,
    _backup_run_defaults,
    _clear_media_root,
    _extract_uploads_to_staging,
    _finalize_restore_run,
    _is_restore_internal_dir,
    _mysql_drop_all_preamble,
    _open_sql_dump,
    _reassert_restore_running_after_database_restore,
    _restore_database,
    _restore_old_holder_path,
    _restore_sqlite_database,
    _run_post_restore_commands,
    _safe_extract_tar,
    _swap_staging_to_media,
    _validate_manifest,
    _verify_database_dump,
    _verify_uploads_archive_file,
    _verify_uploads_contents,
    restore_backup,
)
from .scheduler import (  # noqa: E402
    _maybe_recalculate_uploads_size,
    _maybe_run_daily_scheduler_maintenance,
    _run_daily_scheduler_maintenance,
    _run_with_scheduler_heartbeat,
    run_scheduler_tick,
)

__all__ = [
    "MANIFEST_SUFFIX",
    "can_view_system_status",
    "claim_scheduler_operation",
    "create_backup",
    "database_health",
    "delete_backup_by_key",
    "get_active_operations",
    "get_backup_delete_preview",
    "get_backup_dir",
    "get_backup_entry_by_key",
    "get_dir_size",
    "get_media_root",
    "get_readiness_status",
    "get_system_state",
    "get_system_state_readonly",
    "has_active_operation",
    "logger",
    "recalculate_uploads_size",
    "recover_interrupted_restore",
    "recover_stale_running_operations_on_scheduler_start",
    "restore_backup",
    "run_scheduler_tick",
    "scan_backup_manifests",
    "set_system_mode",
]
