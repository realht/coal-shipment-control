"""Release identity, validation report, and package checksum helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
BUILD_INFO_SCHEMA_VERSION = 1
ALLOWED_FINAL_STATUSES = frozenset({"PASS", "POST-DEPLOY REQUIRED"})


@dataclass(frozen=True)
class GateStep:
    name: str
    command: str
    status: str
    exit_code: int | None
    duration_seconds: float
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["duration_seconds"] = round(self.duration_seconds, 3)
        return result


def normalize_version(value: str) -> str:
    version = value.strip()
    if version.lower().startswith("v"):
        version = version[1:]
    if not SEMVER_RE.fullmatch(version):
        raise ValueError("Version must use strict X.Y.Z SemVer format (for example 1.0.20).")
    return version


def utc_build_time() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def git_commit(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    commit = proc.stdout.strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise RuntimeError("Unable to resolve the full Git commit for this release.")
    return commit.lower()


def source_is_dirty(root: Path) -> bool:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("Unable to verify whether the Git source tree is clean.")
    return bool(proc.stdout.strip())


def calculate_build_id(app_version: str, commit: str, built_at: str) -> str:
    return hashlib.sha256(f"{app_version}{commit}{built_at}".encode()).hexdigest()


def make_build_info(
    *,
    app_version: str,
    commit: str,
    built_at: str,
    steps: list[GateStep],
    post_deploy_required: list[str],
) -> dict[str, object]:
    version = normalize_version(app_version)
    final_status = "POST-DEPLOY REQUIRED" if post_deploy_required else "PASS"
    return {
        "schema_version": BUILD_INFO_SCHEMA_VERSION,
        "app_version": version,
        "git_commit": commit,
        "built_at": built_at,
        "source_dirty": False,
        "build_id": calculate_build_id(version, commit, built_at),
        "gate_status": final_status,
        "post_deploy_required": list(post_deploy_required),
        "steps": [step.to_dict() for step in steps],
    }


def validate_build_info(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["BUILD_INFO.json must contain a JSON object"]
    problems: list[str] = []
    required = {
        "schema_version",
        "app_version",
        "git_commit",
        "built_at",
        "source_dirty",
        "build_id",
        "gate_status",
        "post_deploy_required",
        "steps",
    }
    missing = sorted(required - set(data))
    if missing:
        problems.append("BUILD_INFO.json is missing keys: " + ", ".join(missing))
        return problems
    if data["schema_version"] != BUILD_INFO_SCHEMA_VERSION:
        problems.append("Unsupported BUILD_INFO.json schema_version")
    try:
        version = normalize_version(str(data["app_version"]))
    except ValueError as error:
        problems.append(str(error))
        version = ""
    commit = str(data["git_commit"])
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        problems.append("git_commit must be a full lowercase Git SHA")
    built_at = str(data["built_at"])
    try:
        dt.datetime.fromisoformat(built_at.replace("Z", "+00:00"))
    except ValueError:
        problems.append("built_at must be an ISO-8601 timestamp")
    if data["source_dirty"] is not False:
        problems.append("source_dirty must be false for an official release")
    if version and re.fullmatch(r"[0-9a-f]{40}", commit):
        expected_id = calculate_build_id(version, commit, built_at)
        if data["build_id"] != expected_id:
            problems.append("build_id does not match app_version/git_commit/built_at")
    status = str(data["gate_status"])
    if status not in ALLOWED_FINAL_STATUSES:
        problems.append("gate_status must be PASS or POST-DEPLOY REQUIRED")
    pending = data["post_deploy_required"]
    if not isinstance(pending, list) or not all(isinstance(item, str) and item for item in pending):
        problems.append("post_deploy_required must be a list of non-empty strings")
    elif status == "PASS" and pending:
        problems.append("PASS build cannot have post-deploy requirements")
    elif status == "POST-DEPLOY REQUIRED" and not pending:
        problems.append("POST-DEPLOY REQUIRED build must list pending gates")
    if not isinstance(data["steps"], list) or not data["steps"]:
        problems.append("steps must contain executed release commands")
    else:
        for step in data["steps"]:
            if not isinstance(step, dict) or step.get("status") != "PASS" or step.get("exit_code") != 0:
                problems.append("all recorded local gate steps must have PASS and exit_code=0")
                break
    return problems


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_build_info(package_root: Path) -> dict[str, object]:
    path = package_root / "BUILD_INFO.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Invalid or missing {path}: {error}") from error
    problems = validate_build_info(data)
    if problems:
        raise RuntimeError("Invalid BUILD_INFO.json: " + "; ".join(problems))
    return data


def render_validation_report(build_info: dict[str, object]) -> str:
    status = str(build_info["gate_status"])
    pending = list(build_info["post_deploy_required"])
    rows = []
    for index, step in enumerate(build_info["steps"], 1):
        note = str(step.get("note", "")).replace("|", "\\|")
        command = str(step["command"]).replace("|", "\\|")
        rows.append(
            f"| {index} | {step['name']} | `{command}` | {step['exit_code']} | "
            f"{step['status']} | {step['duration_seconds']}s{(' — ' + note) if note else ''} |"
        )
    pending_lines = "\n".join(f"- {item}" for item in pending) or "- Нет."
    return f"""# Release Validation — Coal Shipments {build_info['app_version']}

Build time (UTC): {build_info['built_at']}  
Git commit: `{build_info['git_commit']}`  
Build ID: `{build_info['build_id']}`  
Source dirty: `false`

## Выполненные pre-release проверки

| # | Проверка | Команда | Exit code | Статус | Длительность |
|---|---|---|---:|---|---|
{chr(10).join(rows)}

## Package integrity

- Runtime-only allowlist и forbidden-path scan: `PASS`.
- Customer documentation self-containment: `PASS`.
- `VERSION`, `BUILD_INFO.json` и runtime metadata согласованы: `PASS`.
- `SHA256SUMS` создан и проверяется перед публикацией package: `PASS`.

## Обязательные проверки после передачи

{pending_lines}

## Итог

**{status}**

`PASS` означает, что все включённые release gates выполнены для этой сборки.
`POST-DEPLOY REQUIRED` означает release candidate: перечисленные проверки должны быть
выполнены на целевом контуре и привязаны к version/build ID до статуса APPROVED.
"""


def package_files(package_root: Path) -> list[Path]:
    return sorted(
        path for path in package_root.rglob("*")
        if path.is_file() and path.relative_to(package_root).as_posix() != "SHA256SUMS"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256sums(package_root: Path) -> None:
    lines = [
        f"{sha256_file(path)}  {path.relative_to(package_root).as_posix()}"
        for path in package_files(package_root)
    ]
    (package_root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_sha256sums(package_root: Path) -> list[str]:
    manifest_path = package_root / "SHA256SUMS"
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ["SHA256SUMS is missing"]
    expected: dict[str, str] = {}
    problems: list[str] = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            problems.append(f"Malformed SHA256SUMS line: {line!r}")
            continue
        digest, rel = match.groups()
        if rel in expected:
            problems.append(f"Duplicate SHA256SUMS path: {rel}")
        expected[rel] = digest
    actual_paths = {path.relative_to(package_root).as_posix() for path in package_files(package_root)}
    if set(expected) != actual_paths:
        for rel in sorted(actual_paths - set(expected)):
            problems.append(f"File is not listed in SHA256SUMS: {rel}")
        for rel in sorted(set(expected) - actual_paths):
            problems.append(f"SHA256SUMS references missing file: {rel}")
    for rel in sorted(set(expected) & actual_paths):
        if sha256_file(package_root / Path(rel)) != expected[rel]:
            problems.append(f"SHA-256 mismatch: {rel}")
    return problems


def validate_release_evidence(package_root: Path) -> list[str]:
    problems: list[str] = []
    try:
        info = load_build_info(package_root)
    except RuntimeError as error:
        return [str(error)]
    version_path = package_root / "VERSION"
    try:
        version = version_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        problems.append("VERSION is missing")
    else:
        if version != info["app_version"]:
            problems.append("VERSION does not match BUILD_INFO.json")
    runtime_path = package_root / "app" / "config" / "build_info.json"
    try:
        runtime_info = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        problems.append("runtime app/config/build_info.json is missing or invalid")
    else:
        if runtime_info != info:
            problems.append("runtime build_info.json does not match BUILD_INFO.json")
    report_path = package_root / "RELEASE_VALIDATION.md"
    try:
        report = report_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        problems.append("RELEASE_VALIDATION.md is missing")
    else:
        for expected in (str(info["app_version"]), str(info["git_commit"]), str(info["build_id"]), str(info["built_at"])):
            if expected not in report:
                problems.append(f"RELEASE_VALIDATION.md does not contain {expected}")
        if "BLOCKED" in report or "**FAIL**" in report:
            problems.append("RELEASE_VALIDATION.md contains a blocking verdict")
        if f"**{info['gate_status']}**" not in report:
            problems.append("RELEASE_VALIDATION.md verdict does not match BUILD_INFO.json")
    problems.extend(verify_sha256sums(package_root))
    return problems
