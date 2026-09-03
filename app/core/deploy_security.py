import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


SECURITY_PROFILE_DJANGO_HTTPS = "django_https"
SECURITY_PROFILE_SYNOLOGY_REVERSE_PROXY = "synology_reverse_proxy"

SECURITY_PROFILES = (
    SECURITY_PROFILE_DJANGO_HTTPS,
    SECURITY_PROFILE_SYNOLOGY_REVERSE_PROXY,
)


@dataclass(frozen=True)
class DeploySecurityResult:
    errors: list[str]
    warnings: list[str]


def validate_deploy_security(environ: Mapping[str, str], settings) -> DeploySecurityResult:
    profile = environ.get("DEPLOY_SECURITY_PROFILE")
    if not profile:
        return DeploySecurityResult(
            errors=[
                "DEPLOY_SECURITY_PROFILE must be explicitly set to one of: "
                "django_https, synology_reverse_proxy.",
            ],
            warnings=[],
        )

    if profile not in SECURITY_PROFILES:
        return DeploySecurityResult(
            errors=[
                "DEPLOY_SECURITY_PROFILE must be one of: "
                "django_https, synology_reverse_proxy.",
            ],
            warnings=[],
        )

    errors: list[str] = []
    warnings: list[str] = []

    _validate_secret_key(settings, errors)
    _validate_db_password(environ, errors)
    _validate_healthcheck_allowed_host(settings, errors)
    _validate_build_identity(environ, settings, errors)

    if profile == SECURITY_PROFILE_SYNOLOGY_REVERSE_PROXY:
        _validate_synology_reverse_proxy(environ, errors)
    else:
        _validate_django_https(environ, errors, warnings)

    _validate_proxy_ssl_header(settings, profile, errors)

    return DeploySecurityResult(errors=errors, warnings=warnings)


_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_GIT_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_BUILD_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_BUILD_INFO_FIELDS = {
    "schema_version",
    "app_version",
    "git_commit",
    "built_at",
    "source_dirty",
    "build_id",
}


def _validate_build_identity(
    environ: Mapping[str, str], settings, errors: list[str]
) -> None:
    """Require a complete, immutable release identity in production only."""
    settings_module = environ.get("DJANGO_SETTINGS_MODULE", "") or getattr(
        settings, "SETTINGS_MODULE", ""
    )
    is_production = (
        settings_module.endswith(".prod")
        if settings_module
        else not getattr(settings, "DEBUG", True)
    )
    if not is_production:
        return

    app_version = getattr(settings, "APP_VERSION", "")
    if not isinstance(app_version, str) or not _SEMVER_RE.fullmatch(app_version):
        errors.append(
            "APP_VERSION must be set to a strict SemVer value without a leading 'v'."
        )

    load_error = getattr(settings, "BUILD_INFO_ERROR", "")
    if load_error:
        errors.append(str(load_error))
        return

    build_info = getattr(settings, "BUILD_INFO", None)
    if not isinstance(build_info, dict) or not build_info:
        errors.append("Embedded build metadata is missing or empty.")
        return

    missing = sorted(_REQUIRED_BUILD_INFO_FIELDS - build_info.keys())
    if missing:
        errors.append(
            "Embedded build metadata is incomplete; missing: " + ", ".join(missing) + "."
        )
        return

    if type(build_info["schema_version"]) is not int or build_info["schema_version"] != 1:
        errors.append("Embedded build metadata schema_version must be 1.")

    embedded_version = build_info["app_version"]
    if not isinstance(embedded_version, str) or not _SEMVER_RE.fullmatch(embedded_version):
        errors.append("Embedded app_version must be a strict SemVer value.")
    elif app_version != embedded_version:
        errors.append(
            f"APP_VERSION ({app_version or 'empty'}) does not match embedded "
            f"app_version ({embedded_version})."
        )

    git_commit = build_info["git_commit"]
    if not isinstance(git_commit, str) or not _GIT_COMMIT_RE.fullmatch(git_commit):
        errors.append("Embedded git_commit must be a full 40-character Git commit hash.")

    built_at = build_info["built_at"]
    if not _is_utc_timestamp(built_at):
        errors.append("Embedded built_at must be an ISO-8601 UTC timestamp.")

    if build_info["source_dirty"] is not False:
        errors.append("Embedded source_dirty must be false for a production release.")

    build_id = build_info["build_id"]
    if not isinstance(build_id, str) or not _BUILD_ID_RE.fullmatch(build_id):
        errors.append("Embedded build_id must be a lowercase SHA-256 digest.")
    elif (
        isinstance(embedded_version, str)
        and isinstance(git_commit, str)
        and isinstance(built_at, str)
    ):
        expected_build_id = hashlib.sha256(
            f"{embedded_version}{git_commit}{built_at}".encode()
        ).hexdigest()
        if build_id != expected_build_id:
            errors.append("Embedded build_id does not match the release identity fields.")


def _is_utc_timestamp(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


_DEV_SECRET_KEY = "dev-only-secret-key-not-for-production"
_ENV_EXAMPLE_SECRET_PLACEHOLDERS = {
    "change-me-to-a-64-character-random-secret-key-before-production-0123456789",
}

_DB_PASSWORD_MIN_LENGTH = 16

_FORBIDDEN_DB_PASSWORDS = {
    "replace-with-strong-password",
    "password",
    "123456",
    "admin",
    "coal",
    "coal_app",
    "changeme",
    "change-me",
    "example",
}


def _validate_secret_key(settings, errors: list[str]) -> None:
    key = getattr(settings, "SECRET_KEY", "")
    if not key:
        errors.append("SECRET_KEY is not set.")
    elif key == _DEV_SECRET_KEY:
        errors.append(
            "SECRET_KEY is the insecure dev default; generate a new key for production."
        )
    elif key in _ENV_EXAMPLE_SECRET_PLACEHOLDERS:
        errors.append(
            "SECRET_KEY is the committed sample placeholder; generate a new key for production."
        )
    elif len(key) < 50:
        errors.append("SECRET_KEY is too short (< 50 chars); generate a new key for production.")


def _validate_db_password(environ: Mapping[str, str], errors: list[str]) -> None:
    password = environ.get("DB_PASSWORD", "")
    if not password:
        errors.append("DB_PASSWORD is not set.")
        return

    if password.strip().lower() in _FORBIDDEN_DB_PASSWORDS:
        errors.append(
            "DB_PASSWORD is a known placeholder/weak value; "
            "set a strong unique password for production."
        )
        return

    if len(password) < _DB_PASSWORD_MIN_LENGTH:
        errors.append(
            f"DB_PASSWORD is too short (< {_DB_PASSWORD_MIN_LENGTH} chars); "
            "set a strong unique password for production."
        )
        return

    user = environ.get("DB_USER", "")
    if user and password.lower() == user.lower():
        errors.append("DB_PASSWORD must not be equal to DB_USER.")


def _validate_healthcheck_allowed_host(settings, errors: list[str]) -> None:
    allowed_hosts = getattr(settings, "ALLOWED_HOSTS", None)
    if allowed_hosts is None:
        return

    if "127.0.0.1" not in allowed_hosts:
        errors.append(
            "ALLOWED_HOSTS must include 127.0.0.1 for the Docker healthcheck "
            "endpoint http://127.0.0.1:8000/healthz/."
        )


def _validate_proxy_ssl_header(settings, profile: str, errors: list[str]) -> None:
    header = getattr(settings, "SECURE_PROXY_SSL_HEADER", None)
    if profile == SECURITY_PROFILE_SYNOLOGY_REVERSE_PROXY:
        if header is None:
            errors.append(
                "SECURE_PROXY_SSL_HEADER must be set for the synology_reverse_proxy "
                "profile so Django trusts X-Forwarded-Proto from the reverse proxy."
            )
    else:
        if header is not None:
            errors.append(
                "SECURE_PROXY_SSL_HEADER must not be set for the django_https profile; "
                "without a reverse proxy a client could spoof X-Forwarded-Proto: https "
                "over plain HTTP and bypass SSL redirect / secure cookies."
            )


def _validate_synology_reverse_proxy(environ: Mapping[str, str], errors: list[str]) -> None:
    _require_bool(environ, "SYNOLOGY_REVERSE_PROXY_CONFIRMED", True, errors)
    _require_bool(environ, "SECURE_SSL_REDIRECT", False, errors)
    _require_int(environ, "SECURE_HSTS_SECONDS", 0, errors)
    _require_bool(environ, "SESSION_COOKIE_SECURE", True, errors)
    _require_bool(environ, "CSRF_COOKIE_SECURE", True, errors)
    _require_trusted_proxies(environ, errors)


def _require_trusted_proxies(environ: Mapping[str, str], errors: list[str]) -> None:
    raw = environ.get("TRUSTED_PROXIES", "")
    proxies = [p.strip() for p in raw.split(",") if p.strip()]
    if not proxies:
        errors.append(
            "TRUSTED_PROXIES must list the reverse proxy address (docker gateway) "
            "for the synology_reverse_proxy profile; otherwise client IPs are "
            "logged as the proxy and X-Forwarded-For can be spoofed."
        )


def _validate_django_https(
    environ: Mapping[str, str],
    errors: list[str],
    warnings: list[str],
) -> None:
    _require_bool(environ, "SECURE_SSL_REDIRECT", True, errors)
    _require_bool(environ, "SESSION_COOKIE_SECURE", True, errors)
    _require_bool(environ, "CSRF_COOKIE_SECURE", True, errors)

    hsts_seconds = _require_int_present(environ, "SECURE_HSTS_SECONDS", errors)
    if hsts_seconds == 0:
        warnings.append(
            "SECURE_HSTS_SECONDS=0 is allowed only for initial HTTPS rollout."
        )


def _require_bool(
    environ: Mapping[str, str],
    key: str,
    expected: bool,
    errors: list[str],
) -> None:
    if key not in environ:
        expected_value = _format_bool(expected)
        errors.append(
            f"{key} must be explicitly set to {expected_value} ({key}={expected_value})."
        )
        return

    actual = _parse_bool(environ[key])
    if actual is None:
        errors.append(f"{key} must be {_format_bool(expected)}.")
        return

    if actual is not expected:
        errors.append(f"{key}={_format_bool(expected)} is required.")


def _require_int(
    environ: Mapping[str, str],
    key: str,
    expected: int,
    errors: list[str],
) -> None:
    actual = _require_int_present(environ, key, errors)
    if actual is not None and actual != expected:
        errors.append(f"{key}={expected} is required.")


def _require_int_present(
    environ: Mapping[str, str],
    key: str,
    errors: list[str],
) -> int | None:
    if key not in environ:
        errors.append(f"{key} must be explicitly set.")
        return None

    try:
        return int(environ[key])
    except ValueError:
        errors.append(f"{key} must be an integer.")
        return None


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return None


def _format_bool(value: bool) -> str:
    return "True" if value else "False"
