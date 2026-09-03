from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evidence import (
    GateStep,
    calculate_build_id,
    make_build_info,
    normalize_version,
    validate_release_evidence,
    verify_sha256sums,
)
import package as package_mod
import audit_package


COMMIT = "a" * 40
BUILT_AT = "2026-07-10T10:20:30Z"


class EvidenceTests(unittest.TestCase):
    def test_semver_is_strict_and_build_id_is_deterministic(self) -> None:
        self.assertEqual(normalize_version("v1.2.3"), "1.2.3")
        with self.assertRaises(ValueError):
            normalize_version("1.2")
        self.assertEqual(
            calculate_build_id("1.2.3", COMMIT, BUILT_AT),
            calculate_build_id("1.2.3", COMMIT, BUILT_AT),
        )

    def test_official_package_contains_consistent_evidence_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            output = root / "release" / "output"
            self._minimal_source(root)
            info = self._build_info()

            prepared = package_mod.prepare_official_package(
                root=root,
                output_parent=output,
                name="coal-shipments-1.2.3-test",
                force=False,
                build_info=info,
            )
            target = package_mod.finalize_official_package(
                prepared, build_info=info, force=False,
            )

            self.assertEqual(validate_release_evidence(target), [])
            self.assertEqual(
                audit_package.validate_official_release_output(target, "1.2.3")["build_id"],
                info["build_id"],
            )
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                audit_package.validate_official_release_output(target, "1.2.4")
            self.assertEqual((target / "VERSION").read_text(encoding="utf-8"), "1.2.3\n")
            self.assertIn("APP_VERSION=1.2.3", (target / ".env.example").read_text(encoding="utf-8"))
            (target / "README.md").write_text("tampered\n", encoding="utf-8")
            self.assertTrue(any("SHA-256 mismatch" in item for item in verify_sha256sums(target)))
            (target / "extra.txt").write_text("extra\n", encoding="utf-8")
            self.assertTrue(any("not listed" in item for item in verify_sha256sums(target)))

    def test_scratch_is_marked_and_has_no_release_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._minimal_source(root)
            target = package_mod.build_scratch_package(
                root=root,
                output_parent=root / "release" / "scratch",
                name="scratch",
                force=False,
                dry_run=False,
            )
            self.assertTrue((target / "UNVALIDATED").is_file())
            self.assertFalse((target / "RELEASE_VALIDATION.md").exists())
            self.assertFalse((target / "BUILD_INFO.json").exists())

    @staticmethod
    def _build_info() -> dict[str, object]:
        return make_build_info(
            app_version="1.2.3",
            commit=COMMIT,
            built_at=BUILT_AT,
            steps=[GateStep("tests", "python -m pytest", "PASS", 0, 1.25)],
            post_deploy_required=["target acceptance"],
        )

    @staticmethod
    def _minimal_source(root: Path) -> None:
        files = {
            ".env.example": "APP_VERSION=\nGUNICORN_LIMIT_REQUEST_LINE=4094\n",
            "README.customer.md": "# Customer\n",
            "Dockerfile": "FROM scratch\n",
            "docker-compose.yml": "services: {}\n",
            "package.json": "{}\n",
            "package-lock.json": "{}\n",
            "tailwind.config.js": "module.exports = {}\n",
            "app/config/settings/base.py": "# runtime\n",
            "app/requirements.txt": "Django==5.2.15\n",
            "docs/deployment_env.md": "# Environment\n",
            "docs/wiki/architecture.md": "# Architecture\n",
            "docs/wiki/customer_deployment_checklist.md": "# Deploy\n",
            "docs/wiki/customer_acceptance_record.md": "# Acceptance\n",
            "docs/wiki/operator_checklist.md": "# Operator\n",
            "docs/wiki/runbook.md": "# Runbook\n",
        }
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
