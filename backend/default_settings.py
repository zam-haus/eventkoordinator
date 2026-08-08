"""
Default Django settings (low priority) loaded by Dynaconf.

These values are the Python defaults and can be overridden by:
- settings.toml
- .settings.secrets.toml
- .env
- environment variables
"""

import inspect
import os
from pathlib import Path

import json
import logging
import logging.config
from urllib.error import URLError
from urllib.request import urlopen

from django.conf.global_settings import (
    LOGGING_CONFIG,
    EMAIL_BACKEND,
)
from jwt import PyJWT
from celery.schedules import crontab

logging.info(f"Loading {__name__}")

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = None

ALLOWED_HOSTS = []

CSRF_TRUSTED_ORIGINS = []

INSTALLED_APPS = [
    "project",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apiv1",
    "openid_user_management",
    "mozilla_django_oidc",
    "simple_history",
    "viewflow",
    "mailqueue",
    "sync_pretix",
    "sync_ical",
    "sync_caldav",
    "solo",
    "django_prometheus",
    "polymorphic",
    "django_celery_beat",
    "userdefinedmodel",
    # "ninja", # Does not work, attempts to access nonexistent http://localhost:5173/api/v1/static/ninja/swagger-ui.css
]

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "mozilla_django_oidc.middleware.SessionRefresh",
    "openid_user_management.sudo.SudoModeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

ROOT_URLCONF = "project.urls"

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
    # Jinja2
    {
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "environment": "project.jinja2.environment",
        },
    },
]

WSGI_APPLICATION = "project.wsgi.application"
AUTH_USER_MODEL = "openid_user_management.OpenIDUser"

DATABASES = {}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
# The wall clock users think in. Storage stays UTC (USE_TZ); this is what
# Django reads and renders local times as — admin, forms, and the dashboard
# filter query, where a typed "15.03.2024 19:00" means 19:00 here.
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Europe/Berlin")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = os.environ.get("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles")

CORS_ALLOWED_ORIGINS = []
CORS_ALLOW_CREDENTIALS = False

AUTHENTICATION_BACKENDS = [
    "openid_user_management.auth.OIDCAuthenticationBackend",
    # "django.contrib.auth.backends.ModelBackend",
    "apiv1.auth.PerObjectPermissionBackend",
]

EMAIL_BACKEND = "mailqueue.mailbackend.MailQueueBackend"

FRONTEND_BASE_URL = "https://example.com"

# OIDC defaults. Dynaconf can override any of these values.
OIDC_RP_CLIENT_ID = ""
OIDC_RP_CLIENT_SECRET = ""

OIDC_DISCOVERY_URL = "https://sso.zam.haus/realms/ZAM/.well-known/openid-configuration"
OIDC_DISCOVERY_TIMEOUT_SECONDS = 2.0

OIDC_RP_SIGN_ALGO = "RS256"
OIDC_RP_SCOPES = "openid email profile"
OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS = 3600
OIDC_STORE_ACCESS_TOKEN = True
OIDC_STORE_ID_TOKEN = True
OIDC_OP_LOGOUT_URL = "https://sso.zam.haus/realms/ZAM/protocol/openid-connect/logout?post_logout_redirect_uri={0}&client_id={1}"
OIDC_OP_LOGOUT_URL_METHOD = "openid_user_management.auth.provider_logout"

pyjwt_defaults = list(PyJWT.decode.__defaults__)
leeway_idx = (
    inspect.getfullargspec(PyJWT.decode).args[0].find("leeway")
)  # Find the position of the "leeway" argument)
pyjwt_defaults[leeway_idx] = (
    10  # Allow 10 seconds clock skew / leeway when validating token expiry
)
PyJWT.decode.__defaults__ = tuple(pyjwt_defaults)
# ic(PyJWT.decode.__defaults__)

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
LOGIN_URL = "/oidc/authenticate/"
OIDC_USERNAME_ALGO = "openid_user_management.auth.generate_username"

# Whether Playwright browsers run headless.  True by default; set to False
# in debug/development to see the browser window during test runs.
PLAYWRIGHT_HEADLESS = True

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"
# ================
# TODO Has to be set by nginx reverse proxy serving the TLS certificate, otherwise it can cause issues when running the app without TLS in development.
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = None
SECURE_HSTS_INCLUDE_SUBDOMAINS = None
SECURE_HSTS_PRELOAD = None
# Trust the X-Forwarded-Proto header set by the upstream TLS-terminating reverse proxy.
# This is required so Django builds https:// URLs (e.g. OIDC redirect URIs) correctly.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# ================

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = False
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False

SECURE_CSP = {
    "default-src": ["'self'"],
    "base-uri": ["'self'"],
    "connect-src": ["'self'"],
    "font-src": ["'self'", "https://cdn.jsdelivr.net", "data:"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'self'"],
    "img-src": ["'self'", "data:", "https://django-ninja.dev"],
    "object-src": ["'none'"],
    "script-src": [
        "'self'",
        "https://cdn.jsdelivr.net",
    ],
    "style-src": ["'self'", "https://cdn.jsdelivr.net"],
}
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB limit for file uploads
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# Celery Beat scheduled tasks

CELERY_BEAT_SCHEDULE = {
    "sync-ical-every-hour": {
        "task": "sync_ical.tasks.sync_all_ical_targets",
        "schedule": crontab(minute=0),  # Every hour at :00
        "options": {"queue": "default"},
    },
    "sync-caldav-every-hour": {
        "task": "sync_caldav.tasks.sync_all_caldav_targets",
        "schedule": crontab(minute=30),  # Offset from iCal to spread load
        "options": {"queue": "default"},
    },
}

CELERY_TASK_ROUTES = {}


CELERY_BROKER_URL = "redis://127.0.0.1:6379/0"
CELERY_RESULT_BACKEND = "redis://127.0.0.1:6379/0"

IMPRINT_URL = "https://www.zam.haus/impressum/"
PRIVACY_POLICY_URL = "https://www.zam.haus/datenschutzerklaerung-2/"
ACCOUNT_MANAGEMENT_URL = "https://sso.zam.haus/realms/ZAM/account/"
