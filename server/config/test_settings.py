import os

os.environ["DJANGO_DEBUG"] = "1"
from .settings import *  # noqa: F403

DATABASES = {"default": dj_database_url.parse(os.environ["MATHSTOA_TEST_DATABASE_URL"])} if os.environ.get("MATHSTOA_TEST_DATABASE_URL") else {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_LOGIN_ENABLED = True
PUBLIC_REGISTRATION_ENABLED = True
# Existing feature tests exercise admitted accounts. Approval-specific tests
# explicitly enable the production-default beta admission policy.
ACCOUNT_APPROVAL_REQUIRED = False
PUBLIC_CONTACT_EMAIL = "privacy@example.invalid"
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
AUTHENTICATION_FORM_ORIGINS = ["https://orcid.org", "https://github.com", "https://gitlab.com", "https://university.example"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SOCIALACCOUNT_PROVIDERS = {
    "orcid": {"APPS": [{"client_id": "test-orcid", "secret": "test-orcid-secret"}]},
    "github": {"APPS": [{"client_id": "test-github", "secret": "test-github-secret"}], "SCOPE": [], "OAUTH_PKCE_ENABLED": True},
    "gitlab": {"APPS": [{"client_id": "test-gitlab", "secret": "test-gitlab-secret"}], "OAUTH_PKCE_ENABLED": True},
    "openid_connect": {"SCOPE": ["openid"], "OAUTH_PKCE_ENABLED": True, "APPS": [{
        "provider_id": "institution", "name": "Test University", "client_id": "test-university",
        "secret": "test-university-secret", "settings": {"server_url": "https://university.example", "fetch_userinfo": False},
    }]},
}
