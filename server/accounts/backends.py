from django.contrib.auth.backends import ModelBackend


class PasswordlessModelBackend(ModelBackend):
    """Keep Django permissions and sessions, without password authentication."""

    def authenticate(self, request, **credentials):
        return None
