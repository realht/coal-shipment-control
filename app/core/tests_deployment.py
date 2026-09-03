from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_public_compose_starts_the_synthetic_demo_only():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "seed_portfolio_demo" in compose
    assert "config.settings.dev" in compose
    assert "/volume1" not in compose


def test_public_env_template_does_not_contain_a_deployable_secret():
    env_template = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "replace-with-a-unique-secret" in env_template
    assert "172.17." not in env_template
