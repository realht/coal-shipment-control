from config.settings.dev import *  # noqa: F403

DATABASES["default"]["NAME"] = BASE_DIR.parent / "e2e" / "acceptance.sqlite3"  # noqa: F405
MEDIA_ROOT = BASE_DIR.parent / "e2e" / "artifacts" / "media"  # noqa: F405
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
