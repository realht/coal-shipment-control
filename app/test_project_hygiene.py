"""Public showcase repository hygiene checks."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_showcase_docs_are_present():
    assert (ROOT / "README.md").is_file()
    assert (ROOT / "docs" / "PORTFOLIO_CASE_STUDY.md").is_file()


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
    )
    for relative_path in forbidden:
        assert not (ROOT / relative_path).exists(), relative_path
