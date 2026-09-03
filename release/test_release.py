from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import release as release_mod


class ReleaseOrchestrationTests(unittest.TestCase):
    def test_version_is_required_and_skip_check_is_removed(self) -> None:
        with self.assertRaises(SystemExit):
            release_mod.parse_args([])
        with self.assertRaises(SystemExit):
            release_mod.parse_args(["--version", "1.2.3", "--skip-check"])

    def test_dry_run_does_not_execute_commands_or_package(self) -> None:
        with patch.object(release_mod.subprocess, "run") as run_command, patch.object(
            release_mod.package_mod, "prepare_official_package"
        ) as prepare:
            result = release_mod.main(["--version", "1.2.3", "--dry-run"])
        self.assertEqual(result, 0)
        run_command.assert_not_called()
        prepare.assert_not_called()

    def test_gate_runner_stops_on_first_failed_command(self) -> None:
        runner = release_mod.GateRunner()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            release_mod.subprocess,
            "run",
            return_value=type("Result", (), {"returncode": 7})(),
        ):
            with self.assertRaisesRegex(release_mod.ReleaseFailure, "exit code 7"):
                runner.run("failing step", ["tool", "check"], cwd=Path(tmp))
        self.assertEqual(runner.steps, [])

    def test_deploy_check_rejects_unexpected_warning_id(self) -> None:
        runner = release_mod.GateRunner()
        result = type(
            "Result",
            (),
            {"returncode": 0, "stdout": "?: (security.W999) new warning\n", "stderr": ""},
        )()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            release_mod.subprocess, "run", return_value=result,
        ):
            with self.assertRaisesRegex(release_mod.ReleaseFailure, "security.W999"):
                runner.run(
                    "deploy",
                    ["python", "manage.py", "check", "--deploy"],
                    cwd=Path(tmp),
                    inspect_deploy_warnings=True,
                )


def _match(vid: str, severity: str, fix_state: str) -> dict:
    return {
        "vulnerability": {
            "id": vid,
            "severity": severity,
            "fix": {"state": fix_state},
        }
    }


class GrypePolicyTests(unittest.TestCase):
    def test_empty_report_has_no_findings(self) -> None:
        blocking, warnings = release_mod.evaluate_grype_report({"matches": []}, frozenset())
        self.assertEqual(blocking, [])
        self.assertEqual(warnings, [])

    def test_missing_matches_key_is_safe(self) -> None:
        blocking, warnings = release_mod.evaluate_grype_report({}, frozenset())
        self.assertEqual((blocking, warnings), ([], []))

    def test_critical_fixed_blocks(self) -> None:
        report = {"matches": [_match("CVE-2026-0001", "Critical", "fixed")]}
        blocking, warnings = release_mod.evaluate_grype_report(report, frozenset())
        self.assertEqual(len(blocking), 1)
        self.assertIn("CVE-2026-0001", blocking[0])
        self.assertEqual(warnings, [])

    def test_high_fixed_blocks_case_insensitive(self) -> None:
        report = {"matches": [_match("CVE-2026-0002", "hIgH", "fixed")]}
        blocking, warnings = release_mod.evaluate_grype_report(report, frozenset())
        self.assertEqual(len(blocking), 1)
        self.assertEqual(warnings, [])

    def test_high_not_fixed_is_warning(self) -> None:
        report = {"matches": [_match("CVE-2026-0003", "High", "not-fixed")]}
        blocking, warnings = release_mod.evaluate_grype_report(report, frozenset())
        self.assertEqual(blocking, [])
        self.assertEqual(len(warnings), 1)

    def test_medium_and_low_are_warnings(self) -> None:
        report = {"matches": [
            _match("CVE-2026-0004", "Medium", "fixed"),
            _match("CVE-2026-0005", "Low", "fixed"),
        ]}
        blocking, warnings = release_mod.evaluate_grype_report(report, frozenset())
        self.assertEqual(blocking, [])
        self.assertEqual(len(warnings), 2)

    def test_allowlisted_critical_is_warning_not_blocking(self) -> None:
        report = {"matches": [_match("CVE-2026-0006", "Critical", "fixed")]}
        blocking, warnings = release_mod.evaluate_grype_report(
            report, frozenset({"CVE-2026-0006"}),
        )
        self.assertEqual(blocking, [])
        self.assertEqual(len(warnings), 1)

    def test_default_allowlist_is_empty(self) -> None:
        self.assertEqual(release_mod.ALLOWED_VULNERABILITY_IDS, frozenset())


if __name__ == "__main__":
    unittest.main()
