"""Assemble a source-and-release package for an external audit."""

from __future__ import annotations

import argparse
import datetime as _datetime
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from evidence import load_build_info, normalize_version, validate_release_evidence


EXCLUDED_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp",
        ".venv",
        "backups",
        "env",
        "htmlcov",
        "logs",
        "media",
        "node_modules",
        "staticfiles",
        "uploads",
        "venv",
    }
)

EXCLUDED_TOP_LEVEL_DIRS = frozenset(
    {
        ".claude",
        ".codex",
        ".git",
        ".handoff-test",
        ".playwright-cli",
        ".worktrees",
        "backups",
        "handoff",
        "logs",
        "node_modules",
    }
)

EXCLUDED_RELEASE_DIRS = frozenset({"output", "for_audit", "_for_audit", "__pycache__"})

EXCLUDED_SUFFIXES = frozenset(
    {".log", ".pyc", ".pyo", ".sqlite3", ".sqlite3-journal", ".xls", ".xlsx"}
)
EXCLUDED_SECRET_FILES = frozenset({".env", ".mcp.json", ".mcp.local.json"})


@dataclass(frozen=True)
class AuditPackageResult:
    target: Path
    zip_path: Path | None
    file_count_by_section: dict[str, int]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_secret_name(name: str) -> bool:
    return name in EXCLUDED_SECRET_FILES or (name.startswith(".env.") and name != ".env.example")


def should_include_file(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = rel.parts
    name = parts[-1]

    if is_secret_name(name):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return False
    if parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
        return False
    if parts[0] == "docs" and len(parts) > 1 and parts[1] == "audit":
        return False
    if parts[0] == "release" and len(parts) > 1 and parts[1] in EXCLUDED_RELEASE_DIRS:
        return False
    return True


def collect_source_files(root: Path) -> list[Path]:
    root = root.resolve()
    files: list[Path] = []
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

        dir_names[:] = [name for name in dir_names if name not in EXCLUDED_DIR_NAMES]
        if not rel_parts:
            dir_names[:] = [name for name in dir_names if name not in EXCLUDED_TOP_LEVEL_DIRS]
        if len(rel_parts) == 1 and rel_parts[0] == "release":
            dir_names[:] = [name for name in dir_names if name not in EXCLUDED_RELEASE_DIRS]

        for name in file_names:
            file_path = current_path / name
            if should_include_file(file_path, root):
                files.append(file_path)

    return sorted(files)


def collect_tree_files(root: Path) -> list[Path]:
    root = root.resolve()
    files: list[Path] = []
    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)
        dir_names[:] = [name for name in dir_names if name not in EXCLUDED_DIR_NAMES]
        for name in file_names:
            file_path = current_path / name
            rel = file_path.relative_to(root)
            if is_secret_name(name) or file_path.suffix in EXCLUDED_SUFFIXES:
                continue
            if any(part in EXCLUDED_DIR_NAMES for part in rel.parts[:-1]):
                continue
            files.append(file_path)
    return sorted(files)


def collect_audit_history_files(root: Path) -> list[Path]:
    audit_root = root / "docs" / "audit"
    if not audit_root.is_dir():
        return []
    return collect_tree_files(audit_root)


def latest_release_output(root: Path) -> Path | None:
    output = root / "release" / "output"
    if not output.is_dir():
        return None
    candidates = [path for path in output.iterdir() if path.is_dir()]
    if not candidates:
        return None
    timestamped = [path for path in candidates if path.name.startswith("coal-shipments-")]
    if timestamped:
        return sorted(timestamped, key=lambda path: path.name)[-1]
    return sorted(candidates, key=lambda path: path.stat().st_mtime)[-1]


def release_output_epoch(release_dir: Path) -> float:
    prefix = "coal-shipments-"
    if release_dir.name.startswith(prefix):
        timestamp = release_dir.name.removeprefix(prefix)
        if "-v" in timestamp:
            timestamp = timestamp.split("-v", 1)[0]
        try:
            parsed = _datetime.datetime.strptime(timestamp, "%Y%m%d-%H%M%S")
        except ValueError:
            pass
        else:
            return parsed.timestamp()
    return release_dir.stat().st_mtime


def latest_source_epoch(root: Path) -> int | None:
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%ct"],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    if not value:
        return None
    return int(value)


def source_has_uncommitted_changes(root: Path) -> bool:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    if proc.returncode != 0:
        return False
    return bool(proc.stdout.strip())


def format_epoch(epoch: float | int | None) -> str:
    if epoch is None:
        return "unknown"
    return _datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def stale_release_instructions() -> str:
    return (
        "Run:\n"
        "  python release/release.py --version X.Y.Z\n"
        "  python release/audit_package.py --version X.Y.Z\n\n"
        "To intentionally package stale release output:\n"
        "  python release/audit_package.py --version X.Y.Z --allow-stale-release-output"
    )


def validate_release_output_is_current(root: Path, release_dir: Path) -> None:
    source_epoch = latest_source_epoch(root)
    release_epoch = release_output_epoch(release_dir)

    if source_has_uncommitted_changes(root):
        raise RuntimeError(
            "Source tree has uncommitted changes, so release_output may not match source/.\n\n"
            f"Selected release output: {release_dir}\n"
            f"Release time: {format_epoch(release_epoch)}\n"
            f"Latest commit: {format_epoch(source_epoch)}\n\n"
            f"{stale_release_instructions()}"
        )

    if source_epoch is not None and release_epoch < source_epoch:
        raise RuntimeError(
            "Selected release output is stale.\n\n"
            f"Selected release output: {release_dir}\n"
            f"Release time: {format_epoch(release_epoch)}\n"
            f"Latest commit: {format_epoch(source_epoch)}\n\n"
            f"{stale_release_instructions()}"
        )


def validate_official_release_output(release_dir: Path, requested_version: str = "") -> dict[str, object]:
    if (release_dir / "UNVALIDATED").exists():
        raise RuntimeError("Scratch/UNVALIDATED package cannot be included in an audit package.")
    build_info = load_build_info(release_dir)
    problems = validate_release_evidence(release_dir)
    if problems:
        raise RuntimeError("Release evidence verification failed:\n  " + "\n  ".join(problems))
    if requested_version:
        normalized = normalize_version(normalize_project_version(requested_version))
        if normalized != build_info["app_version"]:
            raise RuntimeError(
                f"Requested audit version {normalized} does not match release "
                f"BUILD_INFO.json version {build_info['app_version']}."
            )
    return build_info


def copy_files(files: list[Path], *, source_root: Path, target_root: Path, dry_run: bool) -> int:
    for file_path in files:
        rel = file_path.relative_to(source_root)
        dest = target_root / rel
        action = "Would copy" if dry_run else "Copying"
        print(f"  {action}: {rel.as_posix()}")
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, dest)
    return len(files)


def audit_readme(package_name: str, release_dir: Path | None) -> str:
    release_note = release_dir.name if release_dir else "not included"
    generated_at = _datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""# External Audit Package: {package_name}

Generated at: {generated_at}
Included release output: {release_note}

## Audit package structure

### source/

Full source context for external technical audit. This section includes code, tests,
development configs, release scripts, technical documentation, and current wiki files.

### release_output/

The actual customer/runtime release package. This is the cleaned package intended for
installation and operation by the customer. It intentionally excludes tests,
development dependencies, release scripts, internal audit/session notes, and development
history.

Missing dev/test files in `release_output/` are not defects. Audit this section as a
self-contained customer package: check for secrets, internal files, broken links,
commands that cannot run from the package, and contradictions with `source/`.

### audit_history/

Historical audit inputs, corrections, and session notes. This section is only for
retrospective comparison against older risks. Do not treat it as the primary source of
truth without checking current code.

## Suggested audit process

First, audit `source/` independently. Do not rely on `audit_history/` before forming
the initial technical opinion.

Then inspect `release_output/` as a customer/runtime package, not as a developer
handoff.

Finally, review `audit_history/` to check whether prior findings were actually closed.
Treat it as reference context, not as a substitute for a fresh audit.

For request-line and table-filter URL safety, check `GUNICORN_LIMIT_REQUEST_LINE`
and `FILTER_QUERY_SAFE_LIMIT` in `source/docs/deployment_env.md`, then compare
the same policy against `release_output/`.

## Audit objective

Give a direct assessment of where the project stands between MVP and a production-ready
product. Identify blockers, risks, missing verification, and areas that are already
solid. Every finding should cite files, commands, or observed behavior.
"""


def write_audit_readme(target: Path, package_name: str, release_dir: Path | None) -> None:
    (target / "AUDIT_README.md").write_text(
        audit_readme(package_name, release_dir),
        encoding="utf-8",
    )


def write_empty_audit_history_readme(target: Path) -> None:
    history_dir = target / "audit_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / "README.md").write_text(
        "# Audit history\n\n"
        "No audit history files were present in `docs/audit/` when this package was built.\n",
        encoding="utf-8",
    )


def make_zip(target: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(path for path in target.rglob("*") if path.is_file()):
            archive.write(file_path, file_path.relative_to(target.parent))


def build_audit_package(
    *,
    root: Path,
    output_parent: Path,
    name: str,
    force: bool,
    dry_run: bool,
    create_zip: bool,
    release_dir: Path | None,
    allow_stale_release_output: bool = False,
) -> AuditPackageResult:
    root = root.resolve()
    output_parent = output_parent.resolve()
    target = output_parent / name
    zip_path = output_parent / f"{name}.zip" if create_zip else None

    if target.exists():
        if not force:
            raise FileExistsError(f"Audit package already exists: {target}")
        if not dry_run:
            shutil.rmtree(target)

    if zip_path and zip_path.exists() and not force:
        raise FileExistsError(f"Audit zip already exists: {zip_path}")

    selected_release_dir = release_dir.resolve() if release_dir else latest_release_output(root)
    if selected_release_dir and not allow_stale_release_output:
        validate_release_output_is_current(root, selected_release_dir)

    source_files = collect_source_files(root)
    release_files = collect_tree_files(selected_release_dir) if selected_release_dir else []
    audit_history_root = root / "docs" / "audit"
    audit_history_files = collect_audit_history_files(root)

    print(f"Audit package: {target}")
    print(f"Source files: {len(source_files)}")
    source_count = copy_files(
        source_files,
        source_root=root,
        target_root=target / "source",
        dry_run=dry_run,
    )

    release_count = 0
    if selected_release_dir:
        print(f"\nRelease output: {selected_release_dir}")
        print(f"Release files: {len(release_files)}")
        release_count = copy_files(
            release_files,
            source_root=selected_release_dir.parent,
            target_root=target / "release_output",
            dry_run=dry_run,
        )
    else:
        print("\nRelease output: not found")

    print(f"\nAudit history files: {len(audit_history_files)}")
    audit_history_count = copy_files(
        audit_history_files,
        source_root=audit_history_root,
        target_root=target / "audit_history",
        dry_run=dry_run,
    )

    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
        if audit_history_count == 0:
            write_empty_audit_history_readme(target)
        write_audit_readme(target, name, selected_release_dir)
        if zip_path:
            make_zip(target, zip_path)

    return AuditPackageResult(
        target=target,
        zip_path=zip_path,
        file_count_by_section={
            "source": source_count,
            "release_output": release_count,
            "audit_history": audit_history_count,
        },
    )


def normalize_project_version(version: str) -> str:
    value = version.strip().lstrip("\ufeff")
    if not value:
        raise ValueError("Project version is required.")
    if value.startswith(("proj_v", "proj_V")):
        return value[6:]
    if value.startswith(("v", "V")):
        return value[1:]
    return value


def resolve_package_name(args: argparse.Namespace) -> str:
    if args.name:
        return args.name
    version = args.version
    if not version:
        version = input("Project version for audit package (example: 1.0.16): ")
    return f"proj_v{normalize_project_version(version)}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assemble source, release handoff, tests, and docs for external audit.",
    )
    parser.add_argument(
        "--name",
        default="",
        help="Explicit package folder/zip name. Overrides --version.",
    )
    parser.add_argument(
        "--version",
        default="",
        help="Project version for generated package name, e.g. 1.0.16 -> proj_v1.0.16.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=repo_root(),
        help="Repository root. Default: parent of release/.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "for_audit",
        help="Parent directory for audit artifacts. Default: release/for_audit/.",
    )
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=None,
        help="Release output directory to include. Default: latest folder in release/output/.",
    )
    parser.add_argument(
        "--allow-stale-release-output",
        action="store_true",
        help=(
            "Allow packaging when selected release_output is older than source or source has "
            "uncommitted changes. Use only when stale release output is intentional."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing package folder/zip if present.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied without writing anything.",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Create only the folder, not the zip archive.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        name = resolve_package_name(args)
        selected_release = args.release_dir.resolve() if args.release_dir else latest_release_output(args.root)
        if selected_release:
            validate_official_release_output(selected_release, args.version)
        result = build_audit_package(
            root=args.root,
            output_parent=args.output,
            name=name,
            force=args.force,
            dry_run=args.dry_run,
            create_zip=not args.no_zip,
            release_dir=selected_release,
            allow_stale_release_output=args.allow_stale_release_output,
        )
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1

    if args.dry_run:
        print("\nDry run complete.")
    else:
        print(f"\nAudit package folder: {result.target}")
        if result.zip_path:
            print(f"Audit package zip: {result.zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
