import re

from audit.models import AuditLog

from core import system_ops as _ops

from ._shared import _runtime_build_identity, logger


_SEMVER_RE = re.compile(
    r"^v?(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _parse_semver(value, label):
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Restore blocked: {label} app_version is missing")
    match = _SEMVER_RE.fullmatch(value)
    if not match:
        raise RuntimeError(f"Restore blocked: {label} app_version is not valid SemVer: {value!r}")
    major, minor, patch, prerelease, build = match.groups()
    normalized = f"{major}.{minor}.{patch}"
    if prerelease:
        normalized += f"-{prerelease}"
    if build:
        normalized += f"+{build}"
    return {
        "normalized": normalized,
        "core": (int(major), int(minor), int(patch)),
        "prerelease": prerelease.split(".") if prerelease else None,
    }


def _compare_semver(left, right):
    if left["core"] != right["core"]:
        return -1 if left["core"] < right["core"] else 1
    left_pre = left["prerelease"]
    right_pre = right["prerelease"]
    if left_pre is None or right_pre is None:
        if left_pre == right_pre:
            return 0
        return 1 if left_pre is None else -1
    for left_part, right_part in zip(left_pre, right_pre):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_part) < int(right_part) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_part < right_part else 1
    if len(left_pre) == len(right_pre):
        return 0
    return -1 if len(left_pre) < len(right_pre) else 1


def _restore_identity_payload(current_identity, manifests):
    return {
        "current_app_version": current_identity["app_version"],
        "current_app_build_id": current_identity["app_build_id"],
        "backups": [
            {
                "manifest": label,
                "backup_app_version": manifest.get("app_version", ""),
                "backup_app_build_id": manifest.get("app_build_id", ""),
            }
            for label, manifest in manifests
        ],
    }


def _audit_restore_version_decision(restore_run, decision, reason, current_identity, manifests):
    payload = {
        "restore_version_decision": decision,
        "reason": reason,
        **_restore_identity_payload(current_identity, manifests),
    }
    _ops._write_system_audit(
        entity_type=AuditLog.ENTITY_RESTORE,
        entity_id=restore_run.pk,
        action=AuditLog.ACTION_RESTORE_STARTED,
        user=restore_run.initiated_by,
        source=AuditLog.SOURCE_RESTORE,
        new_values=payload,
    )


def _restore_version_preflight(restore_run, full_manifest, incremental_manifest=None):
    """Fail closed on incompatible backup versions before restore mutates data."""
    current_identity = _runtime_build_identity()
    manifests = [("full", full_manifest)]
    if incremental_manifest:
        manifests.append(("incremental", incremental_manifest))

    try:
        current = _parse_semver(current_identity["app_version"], "current application")
        parsed = {
            label: _parse_semver(manifest.get("app_version"), f"{label} backup")
            for label, manifest in manifests
        }
        if incremental_manifest and parsed["full"]["normalized"] != parsed["incremental"]["normalized"]:
            raise RuntimeError(
                "Restore blocked: full and incremental backup app_version values do not match "
                f"({parsed['full']['normalized']} != {parsed['incremental']['normalized']})"
            )

        backup = parsed["incremental" if incremental_manifest else "full"]
        comparison = _compare_semver(backup, current)
        if backup["normalized"] == current["normalized"]:
            return "ALLOW"
        if comparison < 0 and backup["core"][0] == current["core"][0]:
            reason = "Backup was created by an older application version in the same major release"
            logger.warning(reason, extra={"restore_run_id": restore_run.pk})
            _audit_restore_version_decision(restore_run, "WARN", reason, current_identity, manifests)
            return "WARN"
        if backup["core"][0] != current["core"][0]:
            reason = "Backup and current application use different major versions"
        elif comparison > 0:
            reason = "Backup was created by a newer application version"
        else:
            reason = "Backup application version does not exactly match the current build"
        raise RuntimeError(f"Restore blocked: {reason}")
    except RuntimeError as exc:
        if not str(exc).startswith("Restore blocked:"):
            raise
        _audit_restore_version_decision(restore_run, "BLOCK", str(exc), current_identity, manifests)
        raise
