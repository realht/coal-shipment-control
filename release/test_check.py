from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check as check_mod


class CheckTests(unittest.TestCase):
    def test_setup_environment_disables_pip_version_check_for_dependency_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            captured: dict[str, list[str | Path]] = {}

            def fake_run_command(
                label: str,
                args: list[str | Path],
                *,
                cwd: Path | None = None,
                dry_run: bool = False,
            ) -> int:
                captured[label] = args
                return 0

            with patch.object(check_mod, "run_command", side_effect=fake_run_command):
                check_mod.setup_environment(root, dry_run=True)

            self.assertIn(
                "--disable-pip-version-check",
                captured["Install development requirements"],
            )

    def test_check_command_disables_pip_version_check_for_dependency_check(self) -> None:
        python = Path(".tmp/qa-venv/Scripts/python.exe")

        steps = dict(check_mod.command_steps("check", python))

        self.assertIn("--disable-pip-version-check", steps["Pip dependency check"])

    def test_release_contract_check_rejects_missing_filter_limit_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / ".env.example", "GUNICORN_WORKERS=2\n")
            self._write(root / "deploy" / "entrypoint.sh", "exec gunicorn app\n")
            self._write(root / "docs" / "deployment_env.md", "# Env\n")
            self._write(root / "app" / "templates" / "shipments_auto" / "list.html", "")
            self._write(root / "app" / "templates" / "shipments_rail" / "list.html", "")
            self._write(root / "app" / "static" / "js" / "column_filters.js", "")

            problems = check_mod.release_contract_problems(root)

            self.assertTrue(any("GUNICORN_LIMIT_REQUEST_LINE=4094" in p for p in problems))
            self.assertTrue(any("--limit-request-line" in p for p in problems))
            self.assertTrue(any("data-filter-query-safe-limit" in p for p in problems))

    def test_release_contract_check_accepts_filter_limit_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / ".env.example", "GUNICORN_LIMIT_REQUEST_LINE=4094\n")
            self._write(
                root / "deploy" / "entrypoint.sh",
                '--limit-request-line "${GUNICORN_LIMIT_REQUEST_LINE:-4094}"\n',
            )
            self._write(
                root / "docs" / "deployment_env.md",
                "GUNICORN_LIMIT_REQUEST_LINE\nFILTER_QUERY_SAFE_LIMIT\n",
            )
            self._write(
                root / "app" / "templates" / "shipments_auto" / "list.html",
                'data-filter-query-safe-limit="{{ filter_query_safe_limit }}"\n',
            )
            self._write(
                root / "app" / "templates" / "shipments_rail" / "list.html",
                'data-filter-query-safe-limit="{{ filter_query_safe_limit }}"\n',
            )
            self._write(
                root / "app" / "static" / "js" / "column_filters.js",
                "filterQuerySafeLimit\nnewUrl.length > filterQuerySafeLimit\n",
            )

            self.assertEqual(check_mod.release_contract_problems(root), [])

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
