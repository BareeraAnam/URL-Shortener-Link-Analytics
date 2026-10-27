"""Integration settings: REAL Postgres + REAL Redis (used by CI, see tests/integration)."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "integration-secret-key")

from .base import *  # noqa: E402,F403

DEBUG = False
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
ANALYTICS_CACHE_TTL = 0
SHORT_BASE_URL = "http://sho.rt"
ALLOWED_HOSTS = ["testserver", "localhost", "sho.rt"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SESSION_ENGINE = "django.contrib.sessions.backends.db"
GEOIP_COUNTRY_DB = ""
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405
LOGGING = {"version": 1, "disable_existing_loggers": False}
