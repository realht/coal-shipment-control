from __future__ import annotations
import unittest
from acceptance import REQUIRED_STEPS, validate_acceptance_report

BUILD = {"app_version": "1.0.20", "build_id": "b" * 64, "git_commit": "a" * 40}


def valid_report() -> dict[str, object]:
    return {
        "schema_version": 1, "status": "PASS", "started_at": "2026-07-11T10:00:00Z", "finished_at": "2026-07-11T10:05:00Z",
        "identity": {"version": "1.0.20", "build_id": "b" * 64, "commit": "a" * 40, "image_id": "sha256:" + "c" * 64},
        "database": {"vendor": "MariaDB", "version": "10.11", "user": "coal_acceptance", "grants": ["GRANT SELECT ON `coal_smoke`.* TO `coal_acceptance`"]},
        "steps": [{"name": name, "status": "PASS", "duration_seconds": .1, "error": ""} for name in sorted(REQUIRED_STEPS)],
    }


class AcceptanceEvidenceTests(unittest.TestCase):
    def test_accepts_complete_report(self):
        self.assertEqual(validate_acceptance_report(valid_report(), BUILD), [])

    def test_rejects_identity_root_failed_and_missing(self):
        report = valid_report()
        report["identity"]["build_id"] = "wrong"
        report["database"]["user"] = "root"
        report["steps"] = report["steps"][1:]
        report["steps"][0]["status"] = "FAIL"
        problems = validate_acceptance_report(report, BUILD)
        self.assertTrue(any("build_id" in item for item in problems))
        self.assertTrue(any("non-root" in item for item in problems))
        self.assertTrue(any("not PASS" in item for item in problems))
        self.assertTrue(any("missing required" in item for item in problems))

    def test_rejects_global_grant(self):
        report = valid_report()
        report["database"]["grants"] = ["GRANT ALL ON *.* TO coal_acceptance WITH GRANT OPTION"]
        self.assertTrue(any("global" in item for item in validate_acceptance_report(report, BUILD)))


if __name__ == "__main__":
    unittest.main()
