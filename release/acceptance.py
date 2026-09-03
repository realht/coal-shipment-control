"""Fail-closed validation of target acceptance evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from evidence import load_build_info

REQUIRED_STEPS = frozenset({
    "collectstatic", "migrate", "seed_groups", "seed_field_config", "least-privilege database grants",
    "seed minimal data (user/shipment/document)",
    "pytest suite against MariaDB (import/export, documents, permissions matrix)",
    "full and incremental backup/restore drill", "scheduler overlap and lost-lock drill",
})


def validate_acceptance_report(report: object, build: dict[str, object]) -> list[str]:
    if not isinstance(report, dict):
        return ["acceptance report must be a JSON object"]
    required = {"schema_version", "status", "started_at", "finished_at", "identity", "database", "steps"}
    missing = sorted(required - set(report))
    if missing:
        return ["acceptance report is missing keys: " + ", ".join(missing)]
    problems: list[str] = []
    if report["schema_version"] != 1:
        problems.append("unsupported schema_version")
    if report["status"] != "PASS":
        problems.append("acceptance report status must be PASS")
    for field in ("started_at", "finished_at"):
        try:
            value = dt.datetime.fromisoformat(str(report[field]).replace("Z", "+00:00"))
            if value.tzinfo is None:
                raise ValueError
        except ValueError:
            problems.append(f"{field} must be an ISO-8601 timestamp with timezone")
    identity = report["identity"]
    if not isinstance(identity, dict):
        problems.append("identity must be an object")
    else:
        for field, expected in (("version", build["app_version"]), ("build_id", build["build_id"]), ("commit", build["git_commit"])):
            if identity.get(field) != expected:
                problems.append(f"identity.{field} does not match BUILD_INFO.json")
        if not str(identity.get("image_id", "")).strip():
            problems.append("identity.image_id must be non-empty")
    database = report["database"]
    if not isinstance(database, dict):
        problems.append("database must be an object")
    else:
        if str(database.get("user", "")).strip().lower() in {"", "root"}:
            problems.append("database.user must be a non-root acceptance user")
        if str(database.get("vendor", "")).lower() not in {"mysql", "mariadb"}:
            problems.append("database.vendor must identify MariaDB/MySQL")
        grants = database.get("grants")
        if not isinstance(grants, list) or not grants:
            problems.append("database.grants must be a non-empty list")
        elif any(" ON *.*" in str(item).upper() or "GRANT OPTION" in str(item).upper() for item in grants):
            problems.append("database.grants contains global or grant-option privileges")
    steps = report["steps"]
    if not isinstance(steps, list):
        problems.append("steps must be a list")
    else:
        names = {str(step.get("name", "")) for step in steps if isinstance(step, dict)}
        for step in steps:
            if not isinstance(step, dict) or step.get("status") != "PASS":
                problems.append(f"acceptance step is not PASS: {step.get('name', '<unnamed>') if isinstance(step, dict) else '<invalid>'}")
        absent = sorted(REQUIRED_STEPS - names)
        if absent:
            problems.append("acceptance report is missing required steps: " + ", ".join(absent))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        build = load_build_info(args.package)
        report = json.loads(args.report.read_text(encoding="utf-8"))
        problems = validate_acceptance_report(report, build)
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError) as error:
        problems = [str(error)]
    if problems:
        print("Acceptance evidence FAILED:\n  - " + "\n  - ".join(problems), file=sys.stderr)
        return 1
    print("Acceptance evidence PASS: report matches the release package.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
