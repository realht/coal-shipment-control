from .base import BASE_DIR, env
from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

SECRET_KEY = env("APP_SECRET_KEY", default="mariadb-smoke-secret-key-not-for-production")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME", default="coal_smoke"),
        "USER": env("DB_USER", default="root"),
        "PASSWORD": env("DB_PASSWORD", default="coal-smoke-root-password-001"),
        "HOST": env("DB_HOST", default="mariadb"),
        "PORT": env.int("DB_PORT", default=3306),
        "OPTIONS": {
            "charset": "utf8mb4",
            "sql_mode": "STRICT_TRANS_TABLES",
        },
        # The acceptance user deliberately has no global CREATE DATABASE grant.
        # Bootstrap pre-creates this schema and grants rights only on it.
        "TEST": {"NAME": env("DB_TEST_NAME", default="test_coal_smoke")},
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

AXES_ENABLED = False
