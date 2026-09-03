"""Run project quality commands in a fresh local QA virtual environment."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Sequence


COMMANDS = ("lint", "test", "django-check", "deploy-check", "release-contract", "check")
PYTHON_REQUIRED = (3, 13)
PIP_DISABLE_VERSION_CHECK = "--disable-pip-version-check"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]  # release/ -> repo root


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def format_command(args: Sequence[str | Path]) -> str:
    return " ".join(str(arg) for arg in args)


def print_label(label: str, args: Sequence[str | Path], cwd: Path | None = None) -> None:
    location = f" [cwd: {cwd}]" if cwd else ""
    print(f"\n==> {label}{location}", flush=True)
    print(f"+ {format_command(args)}", flush=True)


def pip_command(python: Path, *args: str | Path) -> list[str | Path]:
    return [python, "-m", "pip", PIP_DISABLE_VERSION_CHECK, *args]


def ensure_bootstrap_python() -> int:
    if sys.version_info[:2] != PYTHON_REQUIRED:
        version = ".".join(str(part) for part in sys.version_info[:3])
        required = ".".join(str(part) for part in PYTHON_REQUIRED)
        print(
            f"Bootstrap Python {required} is required; current Python is {version}.",
            file=sys.stderr,
        )
        return 1
    return 0


def run_command(
    label: str,
    args: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    dry_run: bool = False,
) -> int:
    print_label(label, args, cwd)
    if dry_run:
        return 0
    return subprocess.run([str(arg) for arg in args], cwd=cwd, check=False).returncode


def recreate_venv(root: Path, *, dry_run: bool) -> Path:
    venv_dir = root / ".tmp" / "qa-venv"
    print(f"\n==> Recreate QA virtual environment: {venv_dir}", flush=True)
    if dry_run:
        return venv_dir
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    return venv_dir


def ensure_pytest_tmp_dir(app_dir: Path, *, dry_run: bool) -> None:
    pytest_tmp_dir = app_dir / ".tmp"
    print(f"\n==> Ensure pytest temp directory: {pytest_tmp_dir}", flush=True)
    if not dry_run:
        pytest_tmp_dir.mkdir(parents=True, exist_ok=True)


def setup_environment(root: Path, *, dry_run: bool) -> Path:
    venv_dir = recreate_venv(root, dry_run=dry_run)
    python = venv_python(venv_dir)
    requirements = root / "app" / "requirements-dev.txt"
    result = run_command(
        "Install development requirements",
        pip_command(python, "install", "-r", requirements),
        dry_run=dry_run,
    )
    if result != 0:
        raise SystemExit(result)
    return python


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def release_contract_problems(root: Path) -> list[str]:
    checks = {
        ".env.example": (
            _read_text(root / ".env.example"),
            ("GUNICORN_LIMIT_REQUEST_LINE=4094",),
        ),
        "deploy/entrypoint.sh": (
            _read_text(root / "deploy" / "entrypoint.sh"),
            ('--limit-request-line "${GUNICORN_LIMIT_REQUEST_LINE:-4094}"',),
        ),
        "docs/deployment_env.md": (
            _read_text(root / "docs" / "deployment_env.md"),
            ("GUNICORN_LIMIT_REQUEST_LINE", "FILTER_QUERY_SAFE_LIMIT"),
        ),
        "app/templates/shipments_auto/list.html": (
            _read_text(root / "app" / "templates" / "shipments_auto" / "list.html"),
            ("data-filter-query-safe-limit",),
        ),
        "app/templates/shipments_rail/list.html": (
            _read_text(root / "app" / "templates" / "shipments_rail" / "list.html"),
            ("data-filter-query-safe-limit",),
        ),
        "app/static/js/column_filters.js": (
            _read_text(root / "app" / "static" / "js" / "column_filters.js"),
            ("filterQuerySafeLimit", "newUrl.length > filterQuerySafeLimit"),
        ),
    }
    problems: list[str] = []
    for rel_path, (content, required_values) in checks.items():
        for value in required_values:
            if value not in content:
                problems.append(f"{rel_path}: missing '{value}'")

    if "3500" in checks["app/static/js/column_filters.js"][0]:
        problems.append("app/static/js/column_filters.js: contains hardcoded '3500'")
    return problems


def run_release_contract_check(root: Path) -> int:
    print("\n==> Release contract check", flush=True)
    problems = release_contract_problems(root)
    if problems:
        print("Release contract check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("Release contract check passed.", flush=True)
    return 0


def command_steps(command: str, python: Path) -> list[tuple[str, list[str | Path]]]:
    if command == "lint":
        return [("Ruff lint", [python, "-m", "ruff", "check", "."])]
    if command == "test":
        return [("Pytest", [python, "-m", "pytest"])]
    if command == "django-check":
        return [("Django check", [python, "manage.py", "check"])]
    if command == "deploy-check":
        return [
            (
                "Django deploy check",
                [
                    python,
                    "manage.py",
                    "check",
                    "--deploy",
                    "--settings=config.settings.prod",
                ],
            )
        ]
    if command == "release-contract":
        return []
    if command == "check":
        return [
            ("Pip dependency check", pip_command(python, "check")),
            (
                "Django version",
                [python, "-c", "import django; print(f'Django {django.get_version()}')"],
            ),
            ("Ruff lint", [python, "-m", "ruff", "check", "."]),
            ("Pytest", [python, "-m", "pytest"]),
            ("Django check", [python, "manage.py", "check"]),
        ]
    raise ValueError(f"Unsupported command: {command}")


def run_quality(command: str, *, dry_run: bool) -> int:
    root = repo_root()
    app_dir = root / "app"
    if not app_dir.is_dir():
        print(f"App directory not found: {app_dir}", file=sys.stderr)
        return 1

    if command in {"check", "release-contract"}:
        if dry_run:
            print("\n==> Dry run: release contract check is not executed", flush=True)
        else:
            result = run_release_contract_check(root)
            if result != 0:
                return result
        if command == "release-contract":
            return 0

    if not dry_run:
        result = ensure_bootstrap_python()
        if result != 0:
            return result
    else:
        print("==> Dry run: venv creation, installation, and quality commands are not executed")

    ensure_pytest_tmp_dir(app_dir, dry_run=dry_run)
    python = setup_environment(root, dry_run=dry_run)
    for label, args in command_steps(command, python):
        result = run_command(label, args, cwd=app_dir, dry_run=dry_run)
        if result != 0:
            return result
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run quality commands in a freshly recreated .tmp/qa-venv.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print setup and command steps without creating the venv or running commands",
    )
    parser.add_argument("command", choices=COMMANDS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    return run_quality(args.command, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
