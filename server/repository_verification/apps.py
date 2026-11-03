from django.apps import AppConfig


class RepositoryVerificationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "repository_verification"

    def ready(self):
        from . import signals  # noqa: F401
