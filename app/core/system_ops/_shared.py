import hashlib
import json
import logging
import threading
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from core import system_ops as _ops


MANIFEST_SUFFIX = ".manifest.json"
logger = logging.getLogger("core.system_ops")


# Set while a database dump is being loaded so the scheduler heartbeat thread
# stops writing the SystemState singleton row; a concurrent insert between the
# dump's DROP/CREATE and its INSERT would abort the mysql restore (V17-MED-4).
_scheduler_heartbeat_paused = threading.Event()


def get_backup_dir():
    return Path(getattr(settings, "BACKUP_DIR", "/app/backups"))


def get_media_root():
    return Path(settings.MEDIA_ROOT)


def _runtime_build_identity():
    """Return release identity without requiring release tooling at runtime."""
    build_info = getattr(settings, "BUILD_INFO", {})
    if not isinstance(build_info, dict):
        build_info = {}
    return {
        "app_version": getattr(settings, "APP_VERSION", "") or build_info.get("app_version", "") or "",
        "app_build_id": build_info.get("build_id", "") or getattr(settings, "APP_BUILD_ID", "") or "",
        "app_git_commit": build_info.get("git_commit", "") or getattr(settings, "APP_GIT_COMMIT", "") or "",
        "app_built_at": build_info.get("built_at", "") or getattr(settings, "APP_BUILT_AT", "") or "",
    }


def get_dir_size(path):
    root = Path(path)
    if not root.exists():
        return 0
    total = 0
    for item in root.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _safe_user_pk(user):
    user_pk = getattr(user, "pk", None)
    if not user_pk:
        return None
    try:
        return user_pk if get_user_model().objects.filter(pk=user_pk).exists() else None
    except Exception:
        return None


def _now_label():
    return timezone.localtime().strftime("%Y-%m-%d_%H-%M-%S")


def _iso_now():
    return timezone.localtime().isoformat()


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _uploads_inventory(previous=None, root=None):
    root = root or get_media_root()
    result = {}
    if not root.exists():
        return result
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns
        prev = previous.get(rel) if previous else None
        if prev and prev.get("size") == size and prev.get("mtime_ns") == mtime_ns:
            sha256 = prev["sha256"]
        else:
            try:
                sha256 = _ops._sha256_file(path)
            except OSError:
                # V17-MED-3: файл удалён между stat() и чтением (конкурентная
                # работа во время бэкапа) — исключаем из inventory, как и stat().
                continue
        result[rel] = {
            "size": size,
            "mtime_ns": mtime_ns,
            "sha256": sha256,
        }
    return result


def _write_json(path, data):
    with Path(path).open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)


def _load_json(path):
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)
