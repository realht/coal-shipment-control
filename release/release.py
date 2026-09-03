"""Fail-closed official release pipeline."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence


_release_dir = Path(__file__).resolve().parent
if str(_release_dir) not in sys.path:
    sys.path.insert(0, str(_release_dir))

import check as check_mod  # noqa: E402
import clean as clean_mod  # noqa: E402
import package as package_mod  # noqa: E402
from evidence import (  # noqa: E402
    GateStep,
    git_commit,
    make_build_info,
    normalize_version,
    source_is_dirty,
    utc_build_time,
    write_json,
)


ALLOWED_DEPLOY_WARNING_IDS = frozenset({"security.W004", "security.W008", "caches.W003"})
WARNING_ID_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*\.W\d{3}\b")

# Принятые (отрецензированные) уязвимости, которые НЕ блокируют релиз, даже если
# grype нашёл для них фикс и severity High/Critical. Пополнять ТОЛЬКО осознанно
# принятыми CVE/GHSA id (например "CVE-2026-12345") с обоснованием в decisions.md.
# По умолчанию пусто → любая fixable High/Critical блокирует релиз.
ALLOWED_VULNERABILITY_IDS: frozenset[str] = frozenset()


def evaluate_grype_report(
    report: dict, allowlist: frozenset[str],
) -> tuple[list[str], list[str]]:
    """Классифицирует находки grype по согласованной fail/warn-политике.

    Возвращает (blocking, warnings):
      * blocking — уязвимости с доступным фиксом (fix.state == "fixed") и severity
        ∈ {High, Critical}, чей id НЕ в allowlist;
      * warnings — всё остальное (без фикса, Medium/Low/Unknown, а также
        allowlisted High/Critical).
    severity сравнивается регистронезависимо. Функция чистая (без grype/docker) —
    для юнит-тестирования политики.
    """
    blocking: list[str] = []
    warnings: list[str] = []
    for match in report.get("matches") or []:
        vuln = match.get("vulnerability") or {}
        vid = str(vuln.get("id") or "").strip()
        severity_raw = str(vuln.get("severity") or "").strip()
        fix_state = str((vuln.get("fix") or {}).get("state") or "").strip().lower()
        label = f"{vid or '<unknown>'} ({severity_raw or 'Unknown'}, fix={fix_state or 'unknown'})"
        is_high = severity_raw.lower() in {"high", "critical"}
        is_fixed = fix_state == "fixed"
        if is_high and is_fixed and vid not in allowlist:
            blocking.append(label)
        else:
            warnings.append(label)
    return blocking, warnings


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def format_command(args: Sequence[str | Path]) -> str:
    return " ".join(str(arg) for arg in args)


class ReleaseFailure(RuntimeError):
    pass


class GateRunner:
    def __init__(self) -> None:
        self.steps: list[GateStep] = []

    def record_internal(self, name: str, command: str, function) -> None:
        print(f"\n==> {name}\n+ {command}", flush=True)
        started = time.monotonic()
        try:
            result = function()
        except Exception as error:
            raise ReleaseFailure(f"{name} failed: {error}") from error
        duration = time.monotonic() - started
        if result not in (None, 0, []):
            raise ReleaseFailure(f"{name} failed: {result}")
        self.steps.append(GateStep(name, command, "PASS", 0, duration))

    def run(
        self,
        name: str,
        args: Sequence[str | Path],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        inspect_deploy_warnings: bool = False,
    ) -> None:
        command = format_command(args)
        print(f"\n==> {name} [cwd: {cwd}]\n+ {command}", flush=True)
        started = time.monotonic()
        if inspect_deploy_warnings:
            proc = subprocess.run(
                [str(arg) for arg in args], cwd=cwd, env=env,
                capture_output=True, text=True, check=False,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            if output:
                print(output, end="" if output.endswith("\n") else "\n")
            warning_ids = set(WARNING_ID_RE.findall(output))
            unexpected = sorted(warning_ids - ALLOWED_DEPLOY_WARNING_IDS)
            if proc.returncode == 0 and unexpected:
                raise ReleaseFailure(
                    f"{name} produced unexpected deploy warning IDs: {', '.join(unexpected)}"
                )
            note = "allowed warnings: " + ", ".join(sorted(warning_ids)) if warning_ids else ""
        else:
            proc = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, check=False)
            note = ""
        duration = time.monotonic() - started
        if proc.returncode != 0:
            raise ReleaseFailure(f"{name} failed with exit code {proc.returncode}")
        self.steps.append(GateStep(name, command, "PASS", 0, duration, note))


def controlled_prod_env(root: Path, version: str, build_info_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "APP_SECRET_KEY": "release-gate-only-secret-key-" + "x" * 40,
        "ALLOWED_HOSTS": "127.0.0.1,localhost",
        "CSRF_TRUSTED_ORIGINS": "https://localhost",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "3306",
        "DB_NAME": "release_gate",
        "DB_USER": "release_gate",
        "DB_PASSWORD": "release-gate-db-password-2026",
        "DEPLOY_SECURITY_PROFILE": "synology_reverse_proxy",
        "SYNOLOGY_REVERSE_PROXY_CONFIRMED": "True",
        "SECURE_SSL_REDIRECT": "False",
        "SESSION_COOKIE_SECURE": "True",
        "CSRF_COOKIE_SECURE": "True",
        "SECURE_HSTS_SECONDS": "0",
        "TRUSTED_PROXIES": "127.0.0.1",
        "APP_VERSION": version,
        "BUILD_INFO_PATH": str(build_info_path),
        "UPLOADS_DIR": str(root / ".tmp" / "release-uploads"),
        "BACKUP_DIR": str(root / ".tmp" / "release-backups"),
    })
    return env


def npm_executable() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def dry_run_steps(root: Path, version: str) -> list[str]:
    app = root / "app"
    python = check_mod.venv_python(root / ".tmp" / "qa-venv")
    npm = npm_executable()
    return [
        f"validate SemVer={version}, clean Git tree, and full commit",
        "python release/check.py release-contract",
        format_command(check_mod.pip_command(python, "install", "-r", app / "requirements-dev.txt")),
        format_command(check_mod.pip_command(python, "check")),
        format_command([python, "-m", "ruff", "check", "."]),
        format_command([python, "-m", "ruff", "check", "release"]),
        format_command([python, "-m", "pytest", "--basetemp", root / ".tmp" / "pytest-release-PID"]),
        format_command([python, "manage.py", "check"]),
        format_command([python, "manage.py", "makemigrations", "--check", "--dry-run"]),
        format_command([python, "manage.py", "check_deploy_security"]),
        format_command([python, "manage.py", "check", "--deploy"]),
        f"{npm} ci",
        f"{npm} run check:css",
        format_command([python, "-m", "unittest", "discover", "-s", "release", "-p", "test_*.py"]),
        "prepare staging customer package",
        "docker compose config/build and MariaDB smoke when Docker is available",
        "syft SBOM (cyclonedx-json) → sbom.cyclonedx.json when syft is available",
        "grype scan (sbom:… -o json) → vulnerability-report.json; fail on fixable High/Critical",
        "generate and verify VERSION/BUILD_INFO/RELEASE_VALIDATION/SHA256SUMS",
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the fail-closed official release pipeline.")
    parser.add_argument("--version", required=True, help="Release version in strict X.Y.Z format.")
    parser.add_argument("--skip-clean", action="store_true", help="Skip untracked cache cleanup only.")
    parser.add_argument("--name", default="", help="Output folder name; defaults to a versioned timestamp.")
    parser.add_argument("--force", action="store_true", help="Replace an existing same-name output after gate PASS.")
    parser.add_argument("--dry-run", action="store_true", help="Print the gate without writes or command execution.")
    return parser.parse_args(argv)


def _docker_available(root: Path) -> bool:
    if shutil.which("docker") is None:
        return False
    proc = subprocess.run(
        ["docker", "info"], cwd=root, capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0


def _run_docker_gates(runner: GateRunner, root: Path, prepared: package_mod.PreparedPackage) -> list[str]:
    if not _docker_available(root):
        return [
            "Docker compose config and image build from the clean handoff",
            "MariaDB smoke/restore drill",
            "Supply-chain SBOM (syft) and vulnerability scan (grype) on the built image",
        ]
    temporary_env = prepared.staging / ".env"
    shutil.copy2(prepared.staging / ".env.example", temporary_env)
    try:
        runner.run("Docker compose config", ["docker", "compose", "config"], cwd=prepared.staging)
        runner.run("Docker customer image build", ["docker", "compose", "build", "app"], cwd=prepared.staging)
        image_proc = subprocess.run(
            ["docker", "compose", "images", "-q", "app"], cwd=prepared.staging,
            capture_output=True, text=True, check=False,
        )
        image_id = image_proc.stdout.strip()
        if image_proc.returncode != 0 or not image_id:
            raise ReleaseFailure("Unable to resolve the customer app image ID for acceptance evidence")
    finally:
        temporary_env.unlink(missing_ok=True)
    build_info = json.loads((prepared.staging / "BUILD_INFO.json").read_text(encoding="utf-8"))
    acceptance_env = os.environ.copy()
    acceptance_env.update({
        "ACCEPTANCE_APP_VERSION": str(build_info["app_version"]),
        "ACCEPTANCE_BUILD_ID": str(build_info["build_id"]),
        "ACCEPTANCE_COMMIT": str(build_info["git_commit"]),
        "ACCEPTANCE_IMAGE_ID": image_id,
    })
    runner.run(
        "Version-bound MariaDB acceptance",
        ["docker", "compose", "-f", "docker-compose.mariadb-test.yml", "run", "--rm", "smoke"],
        cwd=root,
        env=acceptance_env,
    )
    return _run_supply_chain_gates(runner, prepared, image_id)


def _run_supply_chain_gates(
    runner: GateRunner, prepared: package_mod.PreparedPackage, image_id: str,
) -> list[str]:
    """SBOM (syft) + vuln-scan (grype) по образу. Оба инструмента опциональны:
    при отсутствии любого gate не падает, а помечает шаг как post-deploy-required.
    Артефакты пишутся в staging (попадут под SHA256SUMS в finalize)."""
    syft = shutil.which("syft")
    grype = shutil.which("grype")
    if not syft or not grype:
        pending: list[str] = []
        if not syft:
            pending.append(
                "Generate CycloneDX SBOM (syft) for the built customer image and attach to evidence"
            )
        if not grype:
            pending.append(
                "Scan the built customer image (grype) under the fixable-High/Critical fail policy"
            )
        return pending

    sbom_path = prepared.staging / "sbom.cyclonedx.json"
    report_path = prepared.staging / "vulnerability-report.json"

    def _generate_sbom() -> None:
        proc = subprocess.run(
            [syft, image_id, "-o", "cyclonedx-json"],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise ReleaseFailure(f"syft SBOM generation failed: {(proc.stderr or '').strip()}")
        sbom_path.write_text(proc.stdout, encoding="utf-8")

    runner.record_internal(
        "Supply-chain SBOM (syft)",
        f"syft {image_id} -o cyclonedx-json > sbom.cyclonedx.json",
        _generate_sbom,
    )

    def _scan_vulnerabilities() -> None:
        proc = subprocess.run(
            [grype, f"sbom:{sbom_path}", "-o", "json"],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise ReleaseFailure(f"grype scan failed: {(proc.stderr or '').strip()}")
        report_path.write_text(proc.stdout, encoding="utf-8")
        report = json.loads(proc.stdout)
        blocking, warnings = evaluate_grype_report(report, ALLOWED_VULNERABILITY_IDS)
        if warnings:
            print(
                "Vulnerability warnings (non-blocking): " + "; ".join(sorted(warnings)),
                flush=True,
            )
        if blocking:
            raise ReleaseFailure(
                "Fixable High/Critical vulnerabilities block the release: "
                + "; ".join(sorted(blocking))
                + ". Remediate the image or add accepted IDs to ALLOWED_VULNERABILITY_IDS."
            )

    runner.record_internal(
        "Supply-chain vulnerability scan (grype)",
        f"grype sbom:{sbom_path.name} -o json > vulnerability-report.json",
        _scan_vulnerabilities,
    )
    return []


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root()
    try:
        version = normalize_version(args.version)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"Official release dry run for {version}:\n")
        for index, step in enumerate(dry_run_steps(root, version), 1):
            print(f"{index:02d}. {step}")
        print("\nDry run complete; no files or caches were changed.")
        return 0

    runner = GateRunner()
    prepared: package_mod.PreparedPackage | None = None
    try:
        commit = git_commit(root)
        if source_is_dirty(root):
            raise ReleaseFailure("Official releases require a clean Git source tree.")
        built_at = utc_build_time()
        runner.steps.append(GateStep("Release identity preflight", "git status --porcelain && git rev-parse HEAD", "PASS", 0, 0.0))

        if not args.skip_clean:
            runner.record_internal("Clean local caches", "python release/clean.py", lambda: clean_mod.main([]))
        runner.record_internal(
            "Release contract",
            "python release/check.py release-contract",
            lambda: check_mod.run_release_contract_check(root),
        )
        if check_mod.ensure_bootstrap_python() != 0:
            raise ReleaseFailure("Python 3.13 is required for the official release gate")
        venv_dir = check_mod.recreate_venv(root, dry_run=False)
        python = check_mod.venv_python(venv_dir)
        app_dir = root / "app"
        runner.run(
            "Install development requirements",
            check_mod.pip_command(python, "install", "-r", app_dir / "requirements-dev.txt"),
            cwd=root,
        )
        runner.run("Pip dependency check", check_mod.pip_command(python, "check"), cwd=root)
        runner.run("Ruff lint", [python, "-m", "ruff", "check", "."], cwd=app_dir)
        runner.run("Release tooling lint", [python, "-m", "ruff", "check", "release"], cwd=root)
        pytest_base = root / ".tmp" / f"pytest-release-{os.getpid()}"
        runner.run(
            "Full pytest suite",
            [python, "-m", "pytest", "--basetemp", pytest_base],
            cwd=app_dir,
        )
        runner.run("Django system check", [python, "manage.py", "check"], cwd=app_dir)
        runner.run(
            "Migration drift check",
            [python, "manage.py", "makemigrations", "--check", "--dry-run"],
            cwd=app_dir,
        )

        preliminary = make_build_info(
            app_version=version, commit=commit, built_at=built_at,
            steps=runner.steps, post_deploy_required=["Docker availability not evaluated yet"],
        )
        temp_build_info = root / ".tmp" / "release-build-info.json"
        write_json(temp_build_info, preliminary)
        prod_env = controlled_prod_env(root, version, temp_build_info)
        try:
            runner.run(
                "Production identity/security guard",
                [python, "manage.py", "check_deploy_security"],
                cwd=app_dir,
                env=prod_env,
            )
            runner.run(
                "Django deploy check",
                [python, "manage.py", "check", "--deploy"],
                cwd=app_dir,
                env=prod_env,
                inspect_deploy_warnings=True,
            )
        finally:
            temp_build_info.unlink(missing_ok=True)

        npm = npm_executable()
        runner.run("Install frontend dependencies", [npm, "ci"], cwd=root)
        runner.run("Tailwind CSS drift check", [npm, "run", "check:css"], cwd=root)
        runner.run(
            "Release tooling tests",
            [python, "-m", "unittest", "discover", "-s", "release", "-p", "test_*.py"],
            cwd=root,
        )

        initial_pending = ["Target deployment/reverse-proxy/offsite acceptance bound to this build ID"]
        initial_info = make_build_info(
            app_version=version, commit=commit, built_at=built_at,
            steps=runner.steps, post_deploy_required=initial_pending,
        )
        name = args.name or (
            f"coal-shipments-{built_at[:10].replace('-', '')}-"
            f"{built_at[11:19].replace(':', '')}-v{version}"
        )
        prepared = package_mod.prepare_official_package(
            root=root,
            output_parent=root / "release" / "output",
            name=name,
            force=args.force,
            build_info=initial_info,
        )
        docker_pending = _run_docker_gates(runner, root, prepared)
        pending = initial_pending + docker_pending
        final_info = make_build_info(
            app_version=version, commit=commit, built_at=built_at,
            steps=runner.steps, post_deploy_required=pending,
        )
        target = package_mod.finalize_official_package(prepared, build_info=final_info, force=args.force)
        prepared = None
    except (OSError, ReleaseFailure, RuntimeError) as error:
        if prepared is not None:
            shutil.rmtree(prepared.staging, ignore_errors=True)
        print(f"\nRelease FAILED: {error}", file=sys.stderr)
        return 1

    print(f"\nOfficial release candidate created: {target}")
    print(f"Status: {final_info['gate_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
