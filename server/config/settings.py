"""Same-origin MathStoa application. Provider credentials come from the host."""
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
sys.path.insert(0, str(REPO_ROOT))


def flag(name, default=False):
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes"}


DEBUG = flag("DJANGO_DEBUG")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY, or DJANGO_DEBUG=1 for local development.")
    SECRET_KEY = "local-development-only-do-not-use-on-a-public-server-mathstoa"
if not DEBUG and (len(SECRET_KEY) < 50 or SECRET_KEY.startswith("local-development")):
    raise ImproperlyConfigured("Production needs a random DJANGO_SECRET_KEY of at least 50 characters.")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "").split(",") if h.strip()]
# Render assigns the service hostname only when it creates the service. Trust
# that exact platform-supplied host, never every tenant on onrender.com.
render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").lower()
render_origin = ""
if render_hostname:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.onrender\.com", render_hostname):
        raise ImproperlyConfigured("RENDER_EXTERNAL_HOSTNAME must be an exact onrender.com service hostname.")
    render_origin = f"https://{render_hostname}"
    if render_hostname not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(render_hostname)
render_external_url = os.environ.get("RENDER_EXTERNAL_URL", "")
if render_external_url and (not render_origin or render_external_url not in {render_origin, render_origin + "/"}):
    raise ImproperlyConfigured("RENDER_EXTERNAL_URL must match the HTTPS origin of RENDER_EXTERNAL_HOSTNAME.")
default_public_url = render_external_url or render_origin or ("http://localhost:8000" if DEBUG else "https://math-stoa.org")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", default_public_url).rstrip("/")
public_url = urlsplit(PUBLIC_BASE_URL)
if public_url.username or public_url.password or public_url.query or public_url.fragment or public_url.path:
    raise ImproperlyConfigured("PUBLIC_BASE_URL must be the site's origin, without a path or credentials.")
if not DEBUG and (public_url.scheme != "https" or not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Production requires HTTPS PUBLIC_BASE_URL and explicit DJANGO_ALLOWED_HOSTS.")
CSRF_TRUSTED_ORIGINS = [u.strip() for u in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", PUBLIC_BASE_URL).split(",") if u.strip()]
if render_origin and render_origin not in CSRF_TRUSTED_ORIGINS:
    CSRF_TRUSTED_ORIGINS.append(render_origin)
PUBLIC_CONTACT_EMAIL = os.environ.get("PUBLIC_CONTACT_EMAIL", "")

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "allauth", "allauth.account", "allauth.socialaccount",
    "allauth.socialaccount.providers.orcid", "allauth.socialaccount.providers.github",
    "allauth.socialaccount.providers.gitlab", "allauth.socialaccount.providers.openid_connect",
    "accounts.apps.AccountsConfig", "submissions", "repository_verification.apps.RepositoryVerificationConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware", "accounts.middleware.AccountPolicyMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages", "accounts.context_processors.account_options",
    ]},
}]
DATABASES = {"default": dj_database_url.config(default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=60, conn_health_checks=True)}
if not DEBUG and DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise ImproperlyConfigured("Production needs a persistent PostgreSQL DATABASE_URL.")
CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "mathstoa_cache", "TIMEOUT": 300}}
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend", "allauth.account.auth_backends.AuthenticationBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static", ("site", REPO_ROOT / "docs" / "assets")]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}}
ARCHIVE_PATH = REPO_ROOT / "docs" / "data" / "projects.json"
REPOSITORY_GITHUB_READ_TOKEN = os.environ.get("REPOSITORY_GITHUB_READ_TOKEN", "")
REPOSITORY_VERIFICATION_VALID_DAYS = int(os.environ.get("REPOSITORY_VERIFICATION_VALID_DAYS", "30"))
if not 1 <= REPOSITORY_VERIFICATION_VALID_DAYS <= 30:
    raise ImproperlyConfigured("Repository authorization must expire within 1–30 days.")

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 14 * 24 * 60 * 60
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if flag("DJANGO_SECURE_PROXY_SSL_HEADER"):
    # Only enable behind a proxy which overwrites this header.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024

EMAIL_LOGIN_ENABLED = flag("EMAIL_LOGIN_ENABLED", DEBUG)
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = flag("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = flag("EMAIL_USE_SSL")
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "MathStoa <noreply@localhost>" if DEBUG else "")
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured("Choose either EMAIL_USE_TLS or EMAIL_USE_SSL, not both.")
if not DEBUG and EMAIL_LOGIN_ENABLED and (not EMAIL_HOST or not DEFAULT_FROM_EMAIL):
    raise ImproperlyConfigured("Email sign-in needs EMAIL_HOST and DEFAULT_FROM_EMAIL in production.")

LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "/submissions/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/"
ACCOUNT_ADAPTER = "accounts.adapters.AccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_TIMEOUT = 180
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_MAX_ATTEMPTS = 3
ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_RESEND = True
ACCOUNT_LOGIN_BY_CODE_ENABLED = EMAIL_LOGIN_ENABLED
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 180
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3
ACCOUNT_LOGIN_BY_CODE_SUPPORTS_RESEND = True
ACCOUNT_PREVENT_ENUMERATION = True
ACCOUNT_RATE_LIMITS = {"repository_verification": "5/m/user,10/h/user,5/m/key,10/h/key"}
ACCOUNT_LOGOUT_ON_GET = False
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[MathStoa] "
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"
SOCIALACCOUNT_ADAPTER = "accounts.adapters.MinimalSocialAccountAdapter"
SOCIALACCOUNT_FORMS = {"disconnect": "accounts.forms.PasswordlessDisconnectForm"}
SOCIALACCOUNT_EMAIL_REQUIRED = False
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_QUERY_EMAIL = False
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_PROVIDERS = {}
AUTHENTICATION_FORM_ORIGINS = ["https://orcid.org", "https://sandbox.orcid.org", "https://github.com", "https://gitlab.com"]


def provider_app(provider, **options):
    prefix = provider.upper()
    client_id = os.environ.get(prefix + "_CLIENT_ID", "")
    secret = os.environ.get(prefix + "_CLIENT_SECRET", "")
    if bool(client_id) != bool(secret):
        raise ImproperlyConfigured(f"Set both {prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET.")
    if client_id:
        SOCIALACCOUNT_PROVIDERS[provider] = {"APPS": [{"client_id": client_id, "secret": secret}], **options}


provider_app("orcid", BASE_DOMAIN="sandbox.orcid.org" if flag("ORCID_SANDBOX") else "orcid.org", SCOPE=["/authenticate"])
provider_app("github", SCOPE=[], OAUTH_PKCE_ENABLED=True)
provider_app("gitlab", SCOPE=["read_user"], OAUTH_PKCE_ENABLED=True)
institution_fields = [os.environ.get("INSTITUTION_" + key, "") for key in ("CLIENT_ID", "CLIENT_SECRET", "SERVER_URL")]
if any(institution_fields):
    if not all(institution_fields):
        raise ImproperlyConfigured("Institutional sign-in needs INSTITUTION_CLIENT_ID, CLIENT_SECRET and SERVER_URL.")
    issuer_url = urlsplit(institution_fields[2])
    if issuer_url.scheme != "https" or not issuer_url.hostname or issuer_url.username or issuer_url.password:
        raise ImproperlyConfigured("INSTITUTION_SERVER_URL must be a trusted HTTPS issuer/discovery URL.")
    SOCIALACCOUNT_PROVIDERS["openid_connect"] = {
        "SCOPE": ["openid"], "OAUTH_PKCE_ENABLED": True,
        "APPS": [{"provider_id": "institution", "name": os.environ.get("INSTITUTION_NAME", "University sign-in"),
                  "client_id": institution_fields[0], "secret": institution_fields[1],
                  "settings": {"server_url": institution_fields[2], "fetch_userinfo": False}}],
    }
    authorization_origin = os.environ.get("INSTITUTION_AUTHORIZATION_ORIGIN", f"{issuer_url.scheme}://{issuer_url.netloc}")
    origin = urlsplit(authorization_origin)
    if origin.scheme != "https" or not re.fullmatch(r"[A-Za-z0-9.-]+(?::443)?", origin.netloc) or origin.path or origin.query or origin.fragment:
        raise ImproperlyConfigured("INSTITUTION_AUTHORIZATION_ORIGIN must be an HTTPS origin without a path.")
    AUTHENTICATION_FORM_ORIGINS.append(authorization_origin)
