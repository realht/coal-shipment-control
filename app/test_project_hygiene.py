"""Public showcase repository hygiene checks."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_public_technical_docs_are_present():
    required = (
        "README.md",
        "docs/wiki/README.md",
        "docs/wiki/architecture.md",
        "docs/wiki/data-model.md",
        "docs/wiki/security.md",
        "docs/wiki/quality.md",
        "docs/wiki/operations.md",
    )
    for relative_path in required:
        assert (ROOT / relative_path).is_file(), relative_path


def test_internal_material_is_not_part_of_the_showcase():
    forbidden = (
        ".claude",
        ".codex",
        ".env",
        "docs/wiki/sessions",
        "docs/wiki/backlog.md",
        "docs/wiki/backlog_archive.md",
        "docs/wiki/decisions.md",
        "docs/wiki/next_step.md",
        "docs/INTERVIEW_TALK_TRACK.md",
        "docs/PORTFOLIO_CASE_STUDY.md",
    )
    for relative_path in forbidden:
        assert not (ROOT / relative_path).exists(), relative_path
