"""Base settings. All environment-specific values come from env vars (12-factor)."""

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


# --------------------------------------------------------------------------- core
DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-change-me"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
    "apps.links",
    "apps.analytics",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # Hot path: resolves /<slug> before sessions/auth/CSRF are even touched.
    "apps.links.middleware.RedirectMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASE_URL = env("DATABASE_URL")
if not DATABASE_URL:
    if DEBUG:
        DATABASE_URL = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"  # local development only
    else:
        raise ImproperlyConfigured("DATABASE_URL must be set when DJANGO_DEBUG is false.")
DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=60)}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"

# --------------------------------------------------------------------------- cache / redis
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/1")
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "KEY_PREFIX": "snipr",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SOCKET_CONNECT_TIMEOUT": 1,
            "SOCKET_TIMEOUT": 1,
        },
    }
}
# If Redis is down the redirect still works (falls back to the database).
DJANGO_REDIS_IGNORE_EXCEPTIONS = True
DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS = True
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# --------------------------------------------------------------------------- celery
CELERY_BROKER_URL = env("CELERY_BROKER_URL", env("REDIS_URL", "redis://localhost:6379/0"))
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)  # local dev without a worker
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 8
CELERY_TASK_TIME_LIMIT = 300
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# Publishing must never block a redirect for long when the broker is unavailable.
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_timeout": 2, "socket_connect_timeout": 2}
CELERY_TASK_PUBLISH_RETRY_POLICY = {
    "max_retries": 1,
    "interval_start": 0,
    "interval_step": 0.2,
    "interval_max": 0.2,
}
CELERY_BEAT_SCHEDULE = {
    "purge-old-clicks": {
        "task": "analytics.purge_old_clicks",
        "schedule": timedelta(hours=24),
    },
    "purge-old-import-jobs": {
        "task": "links.purge_old_import_jobs",
        "schedule": timedelta(hours=24),
    },
    "fail-stuck-import-jobs": {
        "task": "links.fail_stuck_import_jobs",
        "schedule": timedelta(minutes=10),
    },
}

# --------------------------------------------------------------------------- DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["apps.core.throttling.GlobalApiThrottle"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
    "URL_FORMAT_OVERRIDE": None,
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Snipr URL Shortener API",
    "DESCRIPTION": "URL shortener with click analytics, QR codes, expiry and password protection.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --------------------------------------------------------------------------- app settings
SHORT_BASE_URL = env("SHORT_BASE_URL", "http://localhost:8000")
AUTO_SLUG_LENGTH = int(env("AUTO_SLUG_LENGTH", "7"))
REDIRECT_STATUS_CODE = int(env("REDIRECT_STATUS_CODE", "302"))  # 302 keeps analytics accurate
LINK_CACHE_TTL = int(env("LINK_CACHE_TTL", "3600"))
MISSING_CACHE_TTL = int(env("MISSING_CACHE_TTL", "60"))
ANALYTICS_CACHE_TTL = int(env("ANALYTICS_CACHE_TTL", "30"))
CLICK_RETENTION_DAYS = int(env("CLICK_RETENTION_DAYS", "365"))
BLOCKED_DOMAINS = env_list("BLOCKED_DOMAINS")
BULK_IMPORT_MAX_ROWS = int(env("BULK_IMPORT_MAX_ROWS", "5000"))
BULK_IMPORT_MAX_BYTES = int(env("BULK_IMPORT_MAX_BYTES", str(2 * 1024 * 1024)))
BULK_IMPORT_SOFT_TIME_LIMIT = int(env("BULK_IMPORT_SOFT_TIME_LIMIT", "1500"))  # seconds; job is marked failed
BULK_IMPORT_TIME_LIMIT = int(env("BULK_IMPORT_TIME_LIMIT", "1560"))  # hard kill, must be > soft limit
BULK_IMPORT_STUCK_MINUTES = int(env("BULK_IMPORT_STUCK_MINUTES", "30"))  # PROCESSING with no progress
BULK_IMPORT_PENDING_MINUTES = int(env("BULK_IMPORT_PENDING_MINUTES", "60"))  # queued but never started
# Each DISTINCT password costs one slow PBKDF2 hash (~0.5-1 s); cap them so a job always fits the time limit.
BULK_IMPORT_MAX_DISTINCT_PASSWORDS = int(env("BULK_IMPORT_MAX_DISTINCT_PASSWORDS", "500"))
BULK_IMPORT_CHUNK = int(env("BULK_IMPORT_CHUNK", "100"))  # progress is saved every N rows
# Deliberate cap so bulk import cannot be used to mint unlimited links (10 imports/h x 5000 rows).
MAX_LINKS_PER_USER = int(env("MAX_LINKS_PER_USER", "10000"))

# Number of trusted reverse proxies in front of the app (nginx, ALB, Cloudflare...).
NUM_PROXIES = int(env("NUM_PROXIES", "0"))
RATELIMIT_IP_META_KEY = "apps.core.utils.get_client_ip"
RATELIMIT_USE_CACHE = "default"
RATELIMIT_FAIL_OPEN = True

REDIRECT_RATE_LIMIT = env("REDIRECT_RATE_LIMIT", "300/m")  # per IP
API_RATE_LIMIT = env("API_RATE_LIMIT", "300/m")  # per user
LINK_CREATE_RATE_LIMIT = env("LINK_CREATE_RATE_LIMIT", "60/m")
BULK_IMPORT_RATE_LIMIT = env("BULK_IMPORT_RATE_LIMIT", "10/h")
AUTH_RATE_LIMIT = env("AUTH_RATE_LIMIT", "10/m")
LOGIN_RATE_LIMIT = env("LOGIN_RATE_LIMIT", "10/m")  # per IP and per username on /login/
UNLOCK_RATE_LIMIT = env("UNLOCK_RATE_LIMIT", "5/m")  # per IP + slug

GEOIP_COUNTRY_DB = env("GEOIP_COUNTRY_DB", str(BASE_DIR / "geoip" / "GeoLite2-Country.mmdb"))

# --------------------------------------------------------------------------- security
if not DEBUG:
    if NUM_PROXIES > 0:  # never trust X-Forwarded-Proto unless a proxy in front sets/strips it
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
    SESSION_COOKIE_SECURE = env_bool("SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = env_bool("SECURE_COOKIES", True)
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "0"))
    SECURE_CONTENT_TYPE_NOSNIFF = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "std"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
