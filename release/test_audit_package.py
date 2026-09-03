from __future__ import annotations

import tempfile
import unittest
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_package
import package as package_mod


class AuditPackageTests(unittest.TestCase):
    def test_source_files_include_tests_and_dev_configs_but_exclude_secrets_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / "README.md")
            self._write(root / ".env", "SECRET_KEY=real-secret")
            self._write(root / "app" / "pytest.ini")
            self._write(root / "app" / "pyproject.toml")
            self._write(root / "app" / "accounts" / "tests.py")
            self._write(root / "app" / ".tmp" / "scratch.txt")
            self._write(root / "docs" / "wiki" / "sessions" / "audit-note.md")
            self._write(root / "docs" / "audit" / "corrections_v15" / "old-audit.md")
            self._write(root / "docs" / "customer-data.xlsx")
            self._write(root / ".handoff-test" / "coal-shipments-test" / "README.md")
            self._write(root / ".worktrees" / "feature" / "README.md")
            self._write(root / "release" / "package.py")
            self._write(root / "release" / "output" / "coal-shipments-old" / "README.md")
            self._write(root / "release" / "for_audit" / "old" / "README.md")
            self._write(root / "release" / "_for_audit" / "old" / "README.md")

            files = {path.relative_to(root).as_posix() for path in audit_package.collect_source_files(root)}

            self.assertIn("README.md", files)
            self.assertIn("app/pytest.ini", files)
            self.assertIn("app/pyproject.toml", files)
            self.assertIn("app/accounts/tests.py", files)
            self.assertIn("docs/wiki/sessions/audit-note.md", files)
            self.assertIn("release/package.py", files)
            self.assertNotIn(".env", files)
            self.assertNotIn("app/.tmp/scratch.txt", files)
            self.assertNotIn("docs/audit/corrections_v15/old-audit.md", files)
            self.assertNotIn("docs/customer-data.xlsx", files)
            self.assertNotIn(".handoff-test/coal-shipments-test/README.md", files)
            self.assertNotIn(".worktrees/feature/README.md", files)
            self.assertNotIn("release/output/coal-shipments-old/README.md", files)
            self.assertNotIn("release/for_audit/old/README.md", files)
            self.assertNotIn("release/_for_audit/old/README.md", files)

    def test_build_audit_package_copies_source_latest_release_output_and_writes_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / "README.md", "# Project")
            self._write(root / "app" / "pytest.ini")
            self._write(root / "app" / "core" / "test_deploy_security.py")
            self._write(root / "release" / "output" / "coal-shipments-20260614-120000" / "README.md")
            self._write(root / "release" / "output" / "coal-shipments-20260615-174001" / "README.md")
            self._write(root / "release" / "output" / "handoff-smoke" / "README.md")

            result = audit_package.build_audit_package(
                root=root,
                output_parent=root / "release" / "for_audit",
                name="proj_v1.0.16",
                force=False,
                dry_run=False,
                create_zip=True,
                release_dir=None,
            )

            self.assertEqual(result.file_count_by_section["source"], 3)
            self.assertTrue((result.target / "source" / "README.md").is_file())
            self.assertTrue((result.target / "source" / "app" / "pytest.ini").is_file())
            self.assertTrue(
                (result.target / "source" / "app" / "core" / "test_deploy_security.py").is_file()
            )
            self.assertTrue(
                (
                    result.target
                    / "release_output"
                    / "coal-shipments-20260615-174001"
                    / "README.md"
                ).is_file()
            )
            self.assertTrue((result.target / "AUDIT_README.md").is_file())
            self.assertTrue((result.target.parent / "proj_v1.0.16.zip").is_file())

    def test_build_audit_package_copies_docs_audit_to_separate_history_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / "README.md", "# Project")
            self._write(root / "docs" / "audit" / "corrections_v15" / "finding.md")
            self._write(
                root / "release" / "output" / "coal-shipments-20260615-174001" / "README.md"
            )

            result = audit_package.build_audit_package(
                root=root,
                output_parent=root / "release" / "for_audit",
                name="proj_v1.0.16",
                force=False,
                dry_run=False,
                create_zip=False,
                release_dir=None,
            )

            self.assertFalse(
                (result.target / "source" / "docs" / "audit" / "corrections_v15" / "finding.md").exists()
            )
            self.assertTrue(
                (result.target / "audit_history" / "corrections_v15" / "finding.md").is_file()
            )
            self.assertEqual(result.file_count_by_section["audit_history"], 1)

    def test_build_audit_package_materializes_empty_audit_history_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / "README.md", "# Project")
            self._write(
                root / "release" / "output" / "coal-shipments-20260615-174001" / "README.md"
            )

            result = audit_package.build_audit_package(
                root=root,
                output_parent=root / "release" / "for_audit",
                name="proj_v1.0.16",
                force=False,
                dry_run=False,
                create_zip=True,
                release_dir=None,
            )

            history_readme = result.target / "audit_history" / "README.md"
            self.assertTrue(history_readme.is_file())
            self.assertIn("No audit history files", history_readme.read_text(encoding="utf-8"))
            with zipfile.ZipFile(result.zip_path) as archive:
                self.assertIn("proj_v1.0.16/audit_history/README.md", archive.namelist())

    def test_build_audit_package_rejects_stale_release_output_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / "README.md", "# Project")
            self._write(
                root / "release" / "output" / "coal-shipments-20260615-120000" / "README.md"
            )

            with patch("audit_package.latest_source_epoch", return_value=1781544953, create=True):
                with patch("audit_package.source_has_uncommitted_changes", return_value=False, create=True):
                    with self.assertRaisesRegex(RuntimeError, "Selected release output is stale"):
                        audit_package.build_audit_package(
                            root=root,
                            output_parent=root / "release" / "for_audit",
                            name="proj_v1.0.16",
                            force=False,
                            dry_run=False,
                            create_zip=False,
                            release_dir=None,
                            allow_stale_release_output=False,
                        )

    def test_build_audit_package_allows_stale_release_output_with_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / "README.md", "# Project")
            self._write(
                root / "release" / "output" / "coal-shipments-20260615-120000" / "README.md"
            )

            with patch("audit_package.latest_source_epoch", return_value=1781544953, create=True):
                with patch("audit_package.source_has_uncommitted_changes", return_value=False, create=True):
                    result = audit_package.build_audit_package(
                        root=root,
                        output_parent=root / "release" / "for_audit",
                        name="proj_v1.0.16",
                        force=False,
                        dry_run=False,
                        create_zip=False,
                        release_dir=None,
                        allow_stale_release_output=True,
                    )

            self.assertTrue(
                (
                    result.target
                    / "release_output"
                    / "coal-shipments-20260615-120000"
                    / "README.md"
                ).is_file()
            )

    def test_build_audit_package_rejects_dirty_source_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root / "README.md", "# Project")
            self._write(
                root / "release" / "output" / "coal-shipments-20260615-120000" / "README.md"
            )

            with patch("audit_package.latest_source_epoch", return_value=None, create=True):
                with patch("audit_package.source_has_uncommitted_changes", return_value=True, create=True):
                    with self.assertRaisesRegex(RuntimeError, "Source tree has uncommitted changes"):
                        audit_package.build_audit_package(
                            root=root,
                            output_parent=root / "release" / "for_audit",
                            name="proj_v1.0.16",
                            force=False,
                            dry_run=False,
                            create_zip=False,
                            release_dir=None,
                            allow_stale_release_output=False,
                        )

    def test_allow_stale_release_output_cli_flag(self) -> None:
        args = audit_package.parse_args(
            ["--version", "1.2.3", "--allow-stale-release-output", "--dry-run"]
        )

        self.assertTrue(args.allow_stale_release_output)

    def test_version_argument_builds_project_package_name(self) -> None:
        args = audit_package.parse_args(["--version", "1.2.3", "--dry-run"])

        self.assertEqual(audit_package.resolve_package_name(args), "proj_v1.2.3")

    def test_version_normalization_removes_prefixes_and_bom(self) -> None:
        self.assertEqual(audit_package.normalize_project_version("\ufeffv1.2.3"), "1.2.3")
        self.assertEqual(audit_package.normalize_project_version("proj_v1.2.3"), "1.2.3")

    def test_missing_name_and_version_prompts_for_version(self) -> None:
        args = audit_package.parse_args(["--dry-run"])

        with patch("builtins.input", return_value="2.0.0") as prompt:
            name = audit_package.resolve_package_name(args)

        self.assertEqual(name, "proj_v2.0.0")
        prompt.assert_called_once()

    def test_customer_package_uses_customer_readme_and_runtime_docs_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "release" / "output"
            self._write(root / "README.md", "# Developer README\n\npython release/check.py check\n")
            self._write(root / "README.customer.md", "# Customer README\n\ndocker compose up -d\n")
            self._write(root / ".env.example", "APP_SECRET_KEY=\nGUNICORN_LIMIT_REQUEST_LINE=4094\n")
            self._write(root / "Dockerfile", "FROM scratch\n")
            self._write(root / "docker-compose.yml", "services: {}\n")
            self._write(root / "package.json", "{}\n")
            self._write(root / "package-lock.json", "{}\n")
            self._write(root / "tailwind.config.js", "module.exports = {}\n")
            self._write(
                root / "RELEASE_VALIDATION.md",
                "# Release validation\n\n"
                "| Ruff lint | `python release/check.py check` (ruff check .) | PASS |\n"
                "| Unit tests | `python release/check.py check` (pytest) | PASS |\n",
            )
            self._write(root / "scripts" / "check-css-drift.mjs", "console.log('dev check')\n")
            self._write(
                root / "docs" / "wiki" / "deployment.md",
                "# Customer checklist\n\ndocker compose ps\n",
            )
            self._write(
                root / "docs" / "wiki" / "production_deployment_acceptance_checklist.md",
                "# Developer checklist\n\npython release/check.py check\npytest\n",
            )
            self._write(root / "docs" / "wiki" / "operations.md", "# Operations\n")
            self._write(root / "docs" / "wiki" / "architecture.md", "# Architecture\n")
            self._write(
                root / "docs" / "deployment_env.md",
                "# Env\n\nGUNICORN_LIMIT_REQUEST_LINE\nFILTER_QUERY_SAFE_LIMIT\n",
            )

            result = package_mod.main(
                [
                    "--scratch",
                    "--root",
                    str(root),
                    "--output",
                    str(output),
                    "--name",
                    "coal-shipments-test",
                ]
            )

            target = output / "coal-shipments-test"
            self.assertEqual(result, 0)
            self.assertEqual(
                (target / "README.md").read_text(encoding="utf-8"),
                "# Customer README\n\ndocker compose up -d\n",
            )
            self.assertFalse((target / "RELEASE_VALIDATION.md").exists())
            self.assertTrue((target / "UNVALIDATED").is_file())
            self.assertIn(
                "GUNICORN_LIMIT_REQUEST_LINE=4094",
                (target / ".env.example").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "FILTER_QUERY_SAFE_LIMIT",
                (target / "docs" / "deployment_env.md").read_text(encoding="utf-8"),
            )
            self.assertTrue((target / "docs" / "wiki" / "deployment.md").is_file())
            self.assertFalse(
                (target / "docs" / "wiki" / "production_deployment_acceptance_checklist.md").exists()
            )
            self.assertFalse((target / "scripts" / "check-css-drift.mjs").exists())

    def test_customer_package_rejects_dev_instructions_and_internal_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "release" / "output"
            self._write(root / "README.md", "# Developer README\n")
            self._write(root / "README.customer.md", "# Customer README\n\ndocker compose ps\n")
            self._write(root / ".env.example", "APP_SECRET_KEY=\nGUNICORN_LIMIT_REQUEST_LINE=4094\n")
            self._write(root / "Dockerfile", "FROM scratch\n")
            self._write(root / "docker-compose.yml", "services: {}\n")
            self._write(root / "package.json", "{}\n")
            self._write(root / "package-lock.json", "{}\n")
            self._write(root / "tailwind.config.js", "module.exports = {}\n")
            self._write(root / "RELEASE_VALIDATION.md", "# Release validation\nне выполнялось\n")
            self._write(root / "app" / "requirements.txt", "Django==5.2.15\n")
            self._write(root / "app" / "requirements-dev.txt", "pytest\n")
            self._write(root / "app" / "pytest.ini", "[pytest]\n")
            self._write(root / "app" / "conftest.py", "")
            self._write(root / "app" / "config" / "settings" / "test_mariadb.py", "")
            self._write(root / "app" / "core" / "management" / "commands" / "mariadb_smoke.py", "")
            self._write(root / "app" / "core" / "tests.py", "")
            self._write(root / "release" / "check.py", "")
            self._write(root / "docs" / "audit" / "finding.md", "")
            self._write(root / "docs" / "wiki" / "sessions" / "note.md", "")
            self._write(root / "docs" / "wiki" / "deployment.md", "# Deployment\n")
            self._write(root / "docs" / "wiki" / "operations.md", "# Operations\n")
            self._write(root / "docs" / "wiki" / "architecture.md", "# Architecture\n")
            self._write(root / "docs" / "deployment_env.md", "# Env\n\nGUNICORN_LIMIT_REQUEST_LINE\n")
            self._write(root / "uploads" / "customer.pdf", "x")
            self._write(root / "backups" / "db.sql", "x")
            self._write(root / ".env", "APP_SECRET_KEY=secret\n")
            self._write(root / "app" / "db.sqlite3", "sqlite")
            self._write(root / "app" / "__pycache__" / "x.pyc", "x")

            result = package_mod.main(
                [
                    "--scratch",
                    "--root",
                    str(root),
                    "--output",
                    str(output),
                    "--name",
                    "coal-shipments-test",
                ]
            )

            target = output / "coal-shipments-test"
            self.assertEqual(result, 0)
            docs_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in sorted(target.rglob("*.md"))
                if path.relative_to(target).as_posix() != "RELEASE_VALIDATION.md"
            )
            forbidden_strings = [
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
            ]
            for value in forbidden_strings:
                self.assertNotIn(value, docs_text)

            forbidden_paths = [
                ".env",
                "app/db.sqlite3",
                "app/requirements-dev.txt",
                "app/pytest.ini",
                "app/conftest.py",
                "app/config/settings/test_mariadb.py",
                "app/core/management/commands/mariadb_smoke.py",
                "release/check.py",
                "docs/wiki/sessions/note.md",
                "docs/audit/finding.md",
                "uploads/customer.pdf",
                "backups/db.sql",
                "app/__pycache__/x.pyc",
            ]
            for rel in forbidden_paths:
                self.assertFalse((target / rel).exists(), f"{rel} must stay out of customer package")

    def test_customer_package_fails_when_customer_docs_contain_dev_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "release" / "output"
            self._write(root / "README.md", "# Developer README\n")
            self._write(root / "README.customer.md", "# Customer README\n\npytest\n")
            self._write(root / ".env.example", "APP_SECRET_KEY=\n")
            self._write(root / "Dockerfile", "FROM scratch\n")
            self._write(root / "docker-compose.yml", "services: {}\n")
            self._write(root / "package.json", "{}\n")
            self._write(root / "package-lock.json", "{}\n")
            self._write(root / "tailwind.config.js", "module.exports = {}\n")
            self._write(root / "RELEASE_VALIDATION.md", "# Release validation\n")
            self._write(root / "docs" / "wiki" / "deployment.md", "# Deployment\n")
            self._write(root / "docs" / "wiki" / "operations.md", "# Operations\n")
            self._write(root / "docs" / "wiki" / "architecture.md", "# Architecture\n")
            self._write(root / "docs" / "deployment_env.md", "# Env\n")

            result = package_mod.main(
                [
                    "--scratch",
                    "--root",
                    str(root),
                    "--output",
                    str(output),
                    "--name",
                    "coal-shipments-test",
                ]
            )

            self.assertEqual(result, 1)
            self.assertFalse((output / "coal-shipments-test").exists())

    def test_release_validation_allows_pre_release_evidence_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            self._write(
                target / "RELEASE_VALIDATION.md",
                "# Release validation\n\n"
                "| Ruff lint | `python release/check.py check` (ruff check .) | PASS |\n"
                "| Unit tests | `python release/check.py check` (pytest) | PASS |\n",
            )
            self._write(target / "README.md", "# Customer README\n\ndocker compose ps\n")

            self.assertEqual(package_mod.check_customer_docs(target), [])

    def test_audit_readme_points_auditor_to_request_line_filter_limit_policy(self) -> None:
        text = audit_package.audit_readme("proj_v1.2.3", None)

        self.assertIn("GUNICORN_LIMIT_REQUEST_LINE", text)
        self.assertIn("FILTER_QUERY_SAFE_LIMIT", text)
        self.assertIn("docs/deployment_env.md", text)

    @staticmethod
    def _write(path: Path, content: str = "x") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
