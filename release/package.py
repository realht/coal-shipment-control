"""Assemble customer handoff folder from local files (no git required)."""

from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from evidence import (
    render_validation_report,
    validate_release_evidence,
    write_json,
    write_sha256sums,
)


POSIX_ROOT_FILES = frozenset({
    ".dockerignore",
    ".env.example",
    "Dockerfile",
    "README.customer.md",
    "docker-compose.yml",
    "package.json",
    "package-lock.json",
    "tailwind.config.js",
})

CUSTOMER_DOCS = frozenset({
    "docs/deployment_env.md",
    "docs/wiki/architecture.md",
    "docs/wiki/customer_deployment_checklist.md",
    "docs/wiki/customer_acceptance_record.md",
    "docs/wiki/operator_checklist.md",
    "docs/wiki/runbook.md",
})

EXCLUDED_APP_EXACT = frozenset({
    "app/Makefile",
    "app/conftest.py",
    "app/core/management/commands/mariadb_smoke.py",
    "app/pytest.ini",
    "app/pyproject.toml",
    "app/requirements-dev.txt",
    "app/result.txt",
    "app/.claudeignore",
    "app/config/settings/dev.py",
    "app/config/settings/test_mariadb.py",
})

EXCLUDED_APP_DIR_PREFIXES = (
    "app/.tmp/",
    "app/.venv/",
    "app/backups/",
    "app/env/",
    "app/media/",
    "app/staticfiles/",
    "app/uploads/",
    "app/venv/",
)

EXCLUDED_DIR_NAMES = frozenset({
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
})

EXCLUDED_SUFFIXES = frozenset({".log", ".pyc", ".pyo", ".sqlite3", ".sqlite3-journal"})

EXCLUDED_SECRET_FILES = frozenset({".env", ".mcp.json", ".mcp.local.json"})

EXCLUDED_TOP_LEVEL_DIRS = frozenset({
    ".claude",
    ".codex",
    ".git",
    ".github",
    ".playwright-cli",
    "backups",
    "handoff",
    "logs",
    "node_modules",
    "release",
})

FORBIDDEN_DIR_NAMES = frozenset({
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".venv", "venv", "env",
})

FORBIDDEN_SUFFIXES = frozenset({".pyc", ".pyo", ".sqlite3", ".sqlite3-journal", ".log"})

FORBIDDEN_SECRET_NAMES = frozenset({".env", ".mcp.json", ".mcp.local.json"})

CUSTOMER_DESTINATION_NAMES = {
    "README.customer.md": "README.md",
}

FORBIDDEN_CUSTOMER_INSTRUCTION_STRINGS = (
    "requirements-dev.txt",
    "release/check.py",
    "release/release.py",
    "release/package.py",
    "pytest",
    "ruff check",
    "npm run check:css",
    "npm ci",
    "conftest.py",
    "pytest.ini",
)

FORBIDDEN_EXACT_PATHS = frozenset({
    ".env",
    ".mcp.json",
    ".mcp.local.json",
    "app/requirements-dev.txt",
    "app/pytest.ini",
    "app/conftest.py",
})

FORBIDDEN_PATH_PREFIXES = (
    "audit_history/",
    "backups/",
    "docs/audit/",
    "docs/wiki/sessions/",
    "media/",
    "node_modules/",
    "release/",
    "uploads/",
)

FORBIDDEN_LOCAL_TOOL_NAMES = frozenset({
    ".claudeignore", ".codexignore", ".mcp.json", ".mcp.local.json",
})

GENERATED_RELEASE_FILES = frozenset({
    "VERSION", "BUILD_INFO.json", "RELEASE_VALIDATION.md", "SHA256SUMS",
})


@dataclass(frozen=True)
class PreparedPackage:
    staging: Path
    target: Path
    file_count: int


def should_include(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = rel.parts
    posix = rel.as_posix()

    for part in parts[:-1]:
        if part in EXCLUDED_DIR_NAMES:
            return False

    if path.suffix in EXCLUDED_SUFFIXES:
        return False

    filename = parts[-1]
    if filename in FORBIDDEN_LOCAL_TOOL_NAMES or filename.startswith((".claude", ".codex")):
        return False
    # .env.example is a public template, not a secret
    if filename in EXCLUDED_SECRET_FILES or (filename.startswith(".env.") and filename != ".env.example"):
        return False

    if len(parts) == 1:
        return posix in POSIX_ROOT_FILES

    top = parts[0]

    if top in EXCLUDED_TOP_LEVEL_DIRS:
        return False

    if top == "deploy":
        return len(parts) == 2 and path.suffix == ".sh"

    if top == "app":
        if posix in EXCLUDED_APP_EXACT:
            return False
        for prefix in EXCLUDED_APP_DIR_PREFIXES:
            if posix.startswith(prefix):
                return False
        if filename.startswith(("test", "tests")) and filename.endswith(".py"):
            return False
        return True

    if top == "docs":
        if len(parts) >= 2 and parts[1] == "audit":
            return False
        return posix in CUSTOMER_DOCS

    if top == "scripts":
        return False

    return False


def collect_files(root: Path) -> list[Path]:
    result = []
    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)
        try:
            rel_parts = current_path.relative_to(root).parts
        except ValueError:
            dir_names[:] = []
            continue

        if rel_parts and rel_parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
            dir_names[:] = []
            continue

        # Исключаем кеш-каталоги на любой глубине
        dir_names[:] = [d for d in dir_names if d not in EXCLUDED_DIR_NAMES]

        # На первом уровне — не входить в top-level excluded dirs
        if not rel_parts:
            dir_names[:] = [d for d in dir_names if d not in EXCLUDED_TOP_LEVEL_DIRS]

        # Внутри docs/ — не входить в локальные audit artifact dirs
        if len(rel_parts) == 1 and rel_parts[0] == "docs":
            dir_names[:] = [d for d in dir_names if d != "audit"]

        for name in file_names:
            file_path = current_path / name
            if should_include(file_path, root):
                result.append(file_path)

    return sorted(result)


def check_forbidden(target: Path) -> list[str]:
    forbidden = []
    for current_root, dir_names, file_names in os.walk(target):
        current_path = Path(current_root)
        rel = current_path.relative_to(target)

        skip = False
        for part in rel.parts:
            if part in FORBIDDEN_DIR_NAMES:
                forbidden.append(str(rel))
                dir_names[:] = []
                skip = True
                break
        if skip:
            continue

        for name in file_names:
            p = current_path / name
            rel_file = p.relative_to(target).as_posix()
            if (
                Path(name).suffix in FORBIDDEN_SUFFIXES
                or name in FORBIDDEN_SECRET_NAMES
                or name in FORBIDDEN_LOCAL_TOOL_NAMES
                or name.startswith((".claude", ".codex"))
                or (name.startswith(".env.") and name != ".env.example")
                or rel_file in FORBIDDEN_EXACT_PATHS
                or any(rel_file.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES)
            ):
                forbidden.append(rel_file)

    return forbidden


def destination_relative_path(source_relative_path: Path) -> Path:
    mapped = CUSTOMER_DESTINATION_NAMES.get(source_relative_path.as_posix())
    if mapped:
        return Path(mapped)
    return source_relative_path


def check_customer_docs(target: Path) -> list[str]:
    problems = []
    for path in sorted(target.rglob("*.md")):
        rel = path.relative_to(target).as_posix()
        text = path.read_text(encoding="utf-8")
        if rel != "RELEASE_VALIDATION.md":
            for value in FORBIDDEN_CUSTOMER_INSTRUCTION_STRINGS:
                if value in text:
                    problems.append(f"{rel}: contains '{value}'")
        if "docs/wiki/sessions/" in text:
            problems.append(f"{rel}: references internal docs/wiki/sessions/")

        link_re = re.compile(r"\[[^\]]+\]\(([^)#?]+)(?:#[^)]*)?\)")
        for match in link_re.finditer(text):
            raw_target = match.group(1).strip()
            if raw_target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / raw_target).resolve()
            try:
                resolved.relative_to(target.resolve())
            except ValueError:
                problems.append(f"{rel}: link escapes package: {raw_target}")
                continue
            if not resolved.exists():
                problems.append(f"{rel}: broken local link: {raw_target}")
    return problems


def _copy_files(files: list[Path], root: Path, target: Path) -> None:
    for source in files:
        rel = source.relative_to(root)
        dest = target / destination_relative_path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


def _write_versioned_env_example(path: Path, version: str) -> None:
    content = path.read_text(encoding="utf-8")
    updated, count = re.subn(r"(?m)^APP_VERSION=.*$", f"APP_VERSION={version}", content)
    if count != 1:
        raise RuntimeError(".env.example must contain exactly one APP_VERSION line")
    path.write_text(updated, encoding="utf-8")


def prepare_official_package(
    *,
    root: Path,
    output_parent: Path,
    name: str,
    force: bool,
    build_info: dict[str, object],
) -> PreparedPackage:
    root = root.resolve()
    output_parent = output_parent.resolve()
    target = output_parent / name
    if target.exists() and not force:
        raise FileExistsError(f"Output folder already exists: {target}")
    output_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{name}-staging-", dir=output_parent))
    try:
        files = collect_files(root)
        if not files:
            raise RuntimeError("No files matched customer package include rules")
        _copy_files(files, root, staging)
        version = str(build_info["app_version"])
        (staging / "VERSION").write_text(version + "\n", encoding="utf-8")
        write_json(staging / "BUILD_INFO.json", build_info)
        write_json(staging / "app" / "config" / "build_info.json", build_info)
        _write_versioned_env_example(staging / ".env.example", version)
        problems = check_forbidden(staging) + check_customer_docs(staging)
        if problems:
            raise RuntimeError("Customer package validation failed:\n  " + "\n  ".join(problems))
        return PreparedPackage(staging=staging, target=target, file_count=len(files))
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def update_staged_build_info(prepared: PreparedPackage, build_info: dict[str, object]) -> None:
    write_json(prepared.staging / "BUILD_INFO.json", build_info)
    write_json(prepared.staging / "app" / "config" / "build_info.json", build_info)


def finalize_official_package(
    prepared: PreparedPackage,
    *,
    build_info: dict[str, object],
    force: bool,
) -> Path:
    staging = prepared.staging
    try:
        update_staged_build_info(prepared, build_info)
        (staging / "RELEASE_VALIDATION.md").write_text(
            render_validation_report(build_info),
            encoding="utf-8",
        )
        write_sha256sums(staging)
        problems = check_forbidden(staging) + check_customer_docs(staging)
        problems.extend(validate_release_evidence(staging))
        if problems:
            raise RuntimeError("Final package validation failed:\n  " + "\n  ".join(problems))
        if prepared.target.exists():
            if not force:
                raise FileExistsError(f"Output folder already exists: {prepared.target}")
            shutil.rmtree(prepared.target)
        staging.replace(prepared.target)
        return prepared.target
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def build_scratch_package(
    *, root: Path, output_parent: Path, name: str, force: bool, dry_run: bool,
) -> Path:
    target = output_parent.resolve() / name
    if target.exists() and not force:
        raise FileExistsError(f"Output folder already exists: {target}")
    files = collect_files(root.resolve())
    if dry_run:
        for path in files:
            print(f"  Would copy: {path.relative_to(root.resolve()).as_posix()}")
        return target
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    _copy_files(files, root.resolve(), target)
    (target / "UNVALIDATED").write_text(
        "This scratch package did not pass the official release gate.\n",
        encoding="utf-8",
    )
    forbidden = check_forbidden(target) + check_customer_docs(target)
    if forbidden:
        shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError("Scratch package contains forbidden paths: " + ", ".join(forbidden))
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble an explicitly UNVALIDATED scratch package.",
    )
    parser.add_argument(
        "--scratch",
        action="store_true",
        help="Required safety flag. Official customer packages are created only by release.py.",
    )
    parser.add_argument(
        "--name",
        default="",
        help="Subfolder name inside release/output/. Default: coal-shipments-YYYYMMDD-HHMMSS.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "scratch",
        help="Parent directory for scratch folders. Default: release/scratch/.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root. Default: parent of release/.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing output folder if it exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied without writing anything.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.scratch:
        print(
            "Direct package.py output is disabled. Use `python release/release.py --version X.Y.Z` "
            "for an official package, or add --scratch for an UNVALIDATED local package.",
            file=sys.stderr,
        )
        return 2
    root = args.root.resolve()
    if not root.is_dir():
        print(f"Root directory not found: {root}", file=sys.stderr)
        return 1
    name = args.name or f"coal-shipments-scratch-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        target = build_scratch_package(
            root=root,
            output_parent=args.output,
            name=name,
            force=args.force,
            dry_run=args.dry_run,
        )
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.dry_run:
        print(f"\nDry run complete. Would create UNVALIDATED scratch package: {target}")
    else:
        print(f"\nUNVALIDATED scratch package created: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
