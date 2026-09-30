"""Standalone timesheets: local review, hosted review, or separately configured production."""
import os
import secrets
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
APP_ENV = os.getenv("APP_ENV", "local")
if APP_ENV not in {"local", "development", "preview", "production"}:
    raise ImproperlyConfigured("APP_ENV must be local, development, preview or production.")
PORTAL_LOCAL = APP_ENV in {"local", "development"}
PORTAL_PREVIEW = APP_ENV in {"local", "preview"}
DEBUG = PORTAL_LOCAL
LOCAL_DATA = BASE_DIR / ".local"
LOCAL_DATA.mkdir(exist_ok=True)


def private_setting(name, filename):
    value = os.getenv(name, "")
    if not value and PORTAL_LOCAL:
        path = LOCAL_DATA / filename
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        value = path.read_text(encoding="utf-8").strip()
    return value


SECRET_KEY = private_setting("DJANGO_SECRET_KEY", "session-secret.txt")
PREVIEW_ACCESS_CODE = private_setting("PREVIEW_ACCESS_CODE", "preview-access-code.txt") if PORTAL_PREVIEW else ""
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("Set a strong DJANGO_SECRET_KEY.")
if PORTAL_PREVIEW and len(PREVIEW_ACCESS_CODE) < 24:
    raise ImproperlyConfigured("Set a private PREVIEW_ACCESS_CODE of at least 24 characters.")
render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
ALLOWED_HOSTS = [host for host in os.getenv("DJANGO_ALLOWED_HOSTS", render_host).split(",") if host]
if PORTAL_LOCAL:
    ALLOWED_HOSTS += ["127.0.0.1", "localhost", "testserver"]
elif not ALLOWED_HOSTS:
    raise ImproperlyConfigured("Set DJANGO_ALLOWED_HOSTS or RENDER_EXTERNAL_HOSTNAME.")
PORTAL_BASE_URL = os.getenv("PORTAL_BASE_URL", f"https://{render_host}" if render_host else "http://127.0.0.1:8010")
CSRF_TRUSTED_ORIGINS = [origin for origin in os.getenv("CSRF_TRUSTED_ORIGINS", PORTAL_BASE_URL).split(",") if origin]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not PORTAL_LOCAL
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
SESSION_COOKIE_SECURE = not PORTAL_LOCAL
CSRF_COOKIE_SECURE = not PORTAL_LOCAL
SECURE_HSTS_SECONDS = 31536000 if not PORTAL_LOCAL else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 8 * 3600
PORTAL_IDLE_SECONDS = 1800
LOGIN_URL = "/staff/login/"
X_FRAME_OPTIONS = "DENY"

INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles", "staff_portal", "website_review",
    "django_otp", "django_otp.plugins.otp_totp",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "staff_portal.security.PortalMiddleware", "staff_portal.preview.PreviewBoundaryMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
              "OPTIONS": {"context_processors": [
                  "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages", "staff_portal.context.portal_context"]}}]
local_database = "development.sqlite3" if APP_ENV == "development" else "preview.sqlite3"
DATABASES = {"default": dj_database_url.config(default=f"sqlite:///{LOCAL_DATA / local_database}", conn_max_age=60)}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"]["OPTIONS"] = {"timeout": 20}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
OTP_TOTP_ISSUER = "Geeza Break Timesheets"
DATA_UPLOAD_MAX_NUMBER_FIELDS = 3000
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
PORTAL_CAPTCHA_MODE = "preview" if PORTAL_PREVIEW else "simulate" if PORTAL_LOCAL else "turnstile"
TURNSTILE_SITE_KEY = os.getenv("TURNSTILE_SITE_KEY", "")
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY", "")
TURNSTILE_HOSTNAMES = [host for host in os.getenv("TURNSTILE_HOSTNAMES", "").split(",") if host]
EMAIL_FILE_PATH = LOCAL_DATA / "emails"
EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend" if PORTAL_PREVIEW or PORTAL_LOCAL else "staff_portal.mail.MailjetBackend"
DEFAULT_FROM_EMAIL = "preview@example.test" if PORTAL_PREVIEW or PORTAL_LOCAL else os.getenv("DEFAULT_FROM_EMAIL", "")
MAILJET_API_KEY = "" if PORTAL_PREVIEW else os.getenv("MAILJET_API_KEY", "")
MAILJET_API_SECRET = "" if PORTAL_PREVIEW else os.getenv("MAILJET_API_SECRET", "")
# Claim notifications can be explicitly enabled in the separate review portal.
# General/invitation email remains isolated in preview, even with inherited live keys.
TIMESHEET_EMAIL_DELIVERY = os.getenv("TIMESHEET_EMAIL_DELIVERY", "preview" if PORTAL_PREVIEW else "mailjet")
if TIMESHEET_EMAIL_DELIVERY not in {"preview", "mailjet"}:
    raise ImproperlyConfigured("Choose preview or mailjet for TIMESHEET_EMAIL_DELIVERY.")
TIMESHEET_MAILJET_API_KEY = os.getenv("TIMESHEET_MAILJET_API_KEY", MAILJET_API_KEY) if TIMESHEET_EMAIL_DELIVERY == "mailjet" else ""
TIMESHEET_MAILJET_API_SECRET = os.getenv("TIMESHEET_MAILJET_API_SECRET", MAILJET_API_SECRET) if TIMESHEET_EMAIL_DELIVERY == "mailjet" else ""
TIMESHEET_FROM_EMAIL = os.getenv("TIMESHEET_FROM_EMAIL", DEFAULT_FROM_EMAIL)
TIMESHEET_NOTIFICATION_EMAIL = os.getenv("TIMESHEET_NOTIFICATION_EMAIL", "finance@example.test" if PORTAL_PREVIEW else "")
if TIMESHEET_EMAIL_DELIVERY == "mailjet":
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    if not all([TIMESHEET_MAILJET_API_KEY, TIMESHEET_MAILJET_API_SECRET]):
        raise ImproperlyConfigured("Configure the timesheet Mailjet credentials before enabling delivery.")
    try:
        validate_email(TIMESHEET_FROM_EMAIL)
        validate_email(TIMESHEET_NOTIFICATION_EMAIL)
        if any(address.lower().endswith(".test") for address in [TIMESHEET_FROM_EMAIL, TIMESHEET_NOTIFICATION_EMAIL]):
            raise ValidationError("Use confirmed deliverable addresses.")
    except ValidationError as exc:
        raise ImproperlyConfigured("Configure a verified timesheet sender and a complete notification email address.") from exc
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "filters": {"redact_links": {"()": "staff_portal.log_filters.RedactAccountLinks"}},
    "handlers": {"portal_console": {"class": "logging.StreamHandler", "filters": ["redact_links"]}},
    "loggers": {
        "django.server": {"handlers": ["portal_console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["portal_console"], "level": "WARNING", "propagate": False},
    },
}
if APP_ENV == "production":
    if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("Live payroll requires persistent PostgreSQL.")
    if not all([TURNSTILE_SITE_KEY, TURNSTILE_SECRET_KEY, TURNSTILE_HOSTNAMES]):
        raise ImproperlyConfigured("Live payroll requires real Turnstile verification.")
    if TURNSTILE_SITE_KEY.startswith(("1x00000000000000000000", "2x00000000000000000000", "3x00000000000000000000")):
        raise ImproperlyConfigured("Use a real Turnstile widget, not a test key, for live payroll.")
    if not all([MAILJET_API_KEY, MAILJET_API_SECRET, DEFAULT_FROM_EMAIL, TIMESHEET_NOTIFICATION_EMAIL]):
        raise ImproperlyConfigured("Configure verified email delivery before live payroll use.")
    if TIMESHEET_EMAIL_DELIVERY != "mailjet":
        raise ImproperlyConfigured("Enable real timesheet notification delivery before live payroll use.")
    if not PORTAL_BASE_URL.startswith("https://"):
        raise ImproperlyConfigured("Live payroll requires an HTTPS portal URL.")
