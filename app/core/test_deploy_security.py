import hashlib
from types import SimpleNamespace

from core.deploy_security import validate_deploy_security

_VALID_SYNOLOGY = {
    "DEPLOY_SECURITY_PROFILE": "synology_reverse_proxy",
    "SYNOLOGY_REVERSE_PROXY_CONFIRMED": "True",
    "SECURE_SSL_REDIRECT": "False",
    "SECURE_HSTS_SECONDS": "0",
    "SESSION_COOKIE_SECURE": "True",
    "CSRF_COOKIE_SECURE": "True",
    "TRUSTED_PROXIES": "172.18.0.1",
    "DB_USER": "coal_app",
    "DB_PASSWORD": "Tr0ub4dor-Zxcvbnm-Strong-2026",
}
_STRONG_KEY = "a" * 50
_ENV_EXAMPLE_PLACEHOLDER = (
    "change-me-to-a-64-character-random-secret-key-before-production-0123456789"
)


_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


def _make_settings(key: str = _STRONG_KEY, proxy_ssl_header=_PROXY_SSL_HEADER):
    # Дефолтный env — synology-профиль, поэтому заголовок задан по умолчанию.
    ns = SimpleNamespace(SECRET_KEY=key)
    if proxy_ssl_header is not None:
        ns.SECURE_PROXY_SSL_HEADER = proxy_ssl_header
    return ns


def _production_settings(**build_info_overrides):
    version = "1.2.3"
    commit = "a" * 40
    built_at = "2026-07-10T09:30:00Z"
    build_info = {
        "schema_version": 1,
        "app_version": version,
        "git_commit": commit,
        "built_at": built_at,
        "source_dirty": False,
        "build_id": hashlib.sha256(f"{version}{commit}{built_at}".encode()).hexdigest(),
    }
    build_info.update(build_info_overrides)
    return SimpleNamespace(
        DEBUG=False,
        APP_VERSION=version,
        BUILD_INFO=build_info,
        BUILD_INFO_ERROR="",
        SECRET_KEY=_STRONG_KEY,
        ALLOWED_HOSTS=["127.0.0.1"],
        SECURE_PROXY_SSL_HEADER=_PROXY_SSL_HEADER,
    )


class TestSecretKeyValidation:
    def test_dev_default_rejected(self):
        result = validate_deploy_security(
            _VALID_SYNOLOGY,
            _make_settings("dev-only-secret-key-not-for-production"),
        )
        assert any("insecure dev default" in e for e in result.errors)

    def test_short_key_rejected(self):
        result = validate_deploy_security(
            _VALID_SYNOLOGY,
            _make_settings("short"),
        )
        assert any("too short" in e for e in result.errors)

    def test_env_example_placeholder_rejected(self):
        result = validate_deploy_security(
            _VALID_SYNOLOGY,
            _make_settings(_ENV_EXAMPLE_PLACEHOLDER),
        )

        assert any("sample placeholder" in e for e in result.errors)

    def test_empty_key_rejected(self):
        result = validate_deploy_security(
            _VALID_SYNOLOGY,
            _make_settings(""),
        )
        assert any("SECRET_KEY" in e for e in result.errors)

    def test_strong_key_no_error(self):
        result = validate_deploy_security(
            _VALID_SYNOLOGY,
            _make_settings(_STRONG_KEY),
        )
        assert not result.errors


_VALID_DJANGO_HTTPS = {
    **_VALID_SYNOLOGY,
    "DEPLOY_SECURITY_PROFILE": "django_https",
    "SECURE_SSL_REDIRECT": "True",
    "SECURE_HSTS_SECONDS": "31536000",
}


class TestProxySSLHeaderValidation:
    def test_synology_with_header_no_error(self):
        result = validate_deploy_security(_VALID_SYNOLOGY, _make_settings())
        assert not result.errors

    def test_synology_without_header_rejected(self):
        result = validate_deploy_security(
            _VALID_SYNOLOGY, _make_settings(proxy_ssl_header=None)
        )
        assert any("SECURE_PROXY_SSL_HEADER must be set" in e for e in result.errors)

    def test_django_https_with_header_rejected(self):
        result = validate_deploy_security(_VALID_DJANGO_HTTPS, _make_settings())
        assert any("SECURE_PROXY_SSL_HEADER must not be set" in e for e in result.errors)

    def test_django_https_without_header_no_error(self):
        result = validate_deploy_security(
            _VALID_DJANGO_HTTPS, _make_settings(proxy_ssl_header=None)
        )
        assert not result.errors


class TestBuildIdentityValidation:
    def test_complete_matching_identity_is_accepted(self):
        result = validate_deploy_security(_VALID_SYNOLOGY, _production_settings())

        assert result.errors == []

    def test_missing_metadata_is_rejected_in_production(self):
        settings = _production_settings()
        settings.BUILD_INFO = {}
        settings.BUILD_INFO_ERROR = "Embedded build metadata is missing."

        result = validate_deploy_security(_VALID_SYNOLOGY, settings)

        assert "Embedded build metadata is missing." in result.errors

    def test_empty_or_invalid_app_version_is_rejected_in_production(self):
        settings = _production_settings()
        settings.APP_VERSION = ""

        result = validate_deploy_security(_VALID_SYNOLOGY, settings)

        assert any("APP_VERSION must be set" in error for error in result.errors)

        settings.APP_VERSION = "v1.2.3"
        result = validate_deploy_security(_VALID_SYNOLOGY, settings)

        assert any("APP_VERSION must be set" in error for error in result.errors)

    def test_mismatched_app_version_is_rejected_in_production(self):
        settings = _production_settings()
        settings.APP_VERSION = "1.2.4"

        result = validate_deploy_security(_VALID_SYNOLOGY, settings)

        assert any("does not match embedded" in error for error in result.errors)

    def test_dirty_metadata_is_rejected_in_production(self):
        result = validate_deploy_security(
            _VALID_SYNOLOGY,
            _production_settings(source_dirty=True),
        )

        assert any("source_dirty must be false" in error for error in result.errors)

    def test_incomplete_metadata_is_rejected_in_production(self):
        settings = _production_settings()
        settings.BUILD_INFO.pop("git_commit")

        result = validate_deploy_security(_VALID_SYNOLOGY, settings)

        assert any("incomplete" in error and "git_commit" in error for error in result.errors)

    def test_inconsistent_build_id_is_rejected_in_production(self):
        result = validate_deploy_security(
            _VALID_SYNOLOGY,
            _production_settings(build_id="0" * 64),
        )

        assert any("does not match" in error for error in result.errors)

    def test_dev_settings_do_not_require_embedded_metadata(self):
        settings = _make_settings()
        settings.DEBUG = False
        settings.SETTINGS_MODULE = "config.settings.dev"

        result = validate_deploy_security(_VALID_SYNOLOGY, settings)

        assert result.errors == []
