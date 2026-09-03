import json
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def load_build_info(path: Path) -> tuple[dict, str]:
    """Load embedded release identity without making dev/test settings unusable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, f"Embedded build metadata is missing: {path}."
    except json.JSONDecodeError as exc:
        return {}, f"Embedded build metadata is not valid JSON: {exc}."
    except OSError as exc:
        return {}, f"Embedded build metadata cannot be read: {exc}."

    if not isinstance(payload, dict):
        return {}, "Embedded build metadata must be a JSON object."
    return payload, ""

env = environ.Env()
environ.Env.read_env(BASE_DIR.parent / ".env")

SECRET_KEY = env("APP_SECRET_KEY", default="")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "core",
    "accounts",
    "catalogs",
    "imports",
    "shipments_auto",
    "shipments_rail",
    "documents",
    "audit",
]

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.RequestBodySizeLimitMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.MaintenanceModeMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.user_flags",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = env("TZ", default="Europe/Moscow")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/uploads/"
MEDIA_ROOT = env("UPLOADS_DIR", default=str(BASE_DIR / "uploads"))

BACKUP_DIR = env("BACKUP_DIR", default=str(BASE_DIR / "backups"))
BACKUP_FULL_KEEP_DAYS = env.int("BACKUP_FULL_KEEP_DAYS", default=30)
BACKUP_INCREMENTAL_KEEP_DAYS = env.int("BACKUP_INCREMENTAL_KEEP_DAYS", default=14)
BACKUP_PRE_RESTORE_KEEP_DAYS = env.int("BACKUP_PRE_RESTORE_KEEP_DAYS", default=30)
BACKUP_MYSQLDUMP_BIN = env("BACKUP_MYSQLDUMP_BIN", default="mysqldump")
BACKUP_MYSQL_BIN = env("BACKUP_MYSQL_BIN", default="mysql")
APP_VERSION = env("APP_VERSION", default="")
DEPLOYED_AT = env("DEPLOYED_AT", default="")
BUILD_INFO_PATH = Path(
    env("BUILD_INFO_PATH", default=str(BASE_DIR / "config" / "build_info.json"))
)
BUILD_INFO, BUILD_INFO_ERROR = load_build_info(BUILD_INFO_PATH)
APP_BUILD_ID = BUILD_INFO.get("build_id", "")
APP_GIT_COMMIT = BUILD_INFO.get("git_commit", "")
APP_BUILT_AT = BUILD_INFO.get("built_at", "")

MAX_UPLOAD_SIZE_MB = env.int("MAX_UPLOAD_SIZE_MB", default=25)

# V18-MED-5: жёсткий лимит тела запроса на уровне app-middleware (второй эшелон
# после reverse-proxy client_max_body_size). Чуть выше MAX_UPLOAD_SIZE_MB, чтобы
# не резать легитимные multipart-загрузки с накладными расходами формы.
MAX_REQUEST_BODY_SIZE_MB = env.int("MAX_REQUEST_BODY_SIZE_MB", default=30)
MAX_REQUEST_BODY_SIZE_BYTES = MAX_REQUEST_BODY_SIZE_MB * 1024 * 1024

MAX_IMPORT_SIZE_MB = env.int("MAX_IMPORT_SIZE_MB", default=10)
MAX_IMPORT_SIZE_BYTES = MAX_IMPORT_SIZE_MB * 1024 * 1024
MAX_IMPORT_UNCOMPRESSED_SIZE_MB = env.int("MAX_IMPORT_UNCOMPRESSED_SIZE_MB", default=50)
MAX_IMPORT_UNCOMPRESSED_SIZE_BYTES = MAX_IMPORT_UNCOMPRESSED_SIZE_MB * 1024 * 1024
MAX_IMPORT_SHARED_STRINGS = env.int("MAX_IMPORT_SHARED_STRINGS", default=100000)
IMPORT_TMP_DIR = env("IMPORT_TMP_DIR", default=str(BASE_DIR / ".tmp" / "import_previews"))
IMPORT_TMP_TTL_HOURS = env.int("IMPORT_TMP_TTL_HOURS", default=24)
IMPORT_ROW_RESULTS_KEEP_DAYS = env.int("IMPORT_ROW_RESULTS_KEEP_DAYS", default=180)
DELETED_DOCUMENT_FILE_KEEP_DAYS = env.int("DELETED_DOCUMENT_FILE_KEEP_DAYS", default=30)
SCHEDULER_HEARTBEAT_INTERVAL_SECONDS = env.int("SCHEDULER_HEARTBEAT_INTERVAL_SECONDS", default=60)
ALLOWED_IMPORT_EXTENSIONS = {"xlsx"}
PARTIAL_EXPORT_MAX_IDS = env.int("PARTIAL_EXPORT_MAX_IDS", default=1000)
FULL_EXPORT_MAX_ROWS = env.int("FULL_EXPORT_MAX_ROWS", default=10000)
GUNICORN_LIMIT_REQUEST_LINE = env.int("GUNICORN_LIMIT_REQUEST_LINE", default=4094)
FILTER_QUERY_SAFETY_MARGIN = env.int("FILTER_QUERY_SAFETY_MARGIN", default=400)
FILTER_QUERY_SAFE_LIMIT = max(0, GUNICORN_LIMIT_REQUEST_LINE - FILTER_QUERY_SAFETY_MARGIN)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

SESSION_COOKIE_AGE = 28800

LOG_LEVEL = env("LOG_LEVEL", default="INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": LOG_LEVEL,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "core": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "imports": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "documents": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
# axes берёт client IP той же функцией, что и аудит (rightmost-untrusted за
# reverse proxy) — иначе ip_address вырождается в адрес прокси (DoS по логину).
AXES_CLIENT_IP_CALLABLE = "core.ip_utils.get_client_ip"

TRUSTED_PROXIES = env.list("TRUSTED_PROXIES", default=[])
