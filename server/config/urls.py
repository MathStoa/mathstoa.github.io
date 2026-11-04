from django.contrib import admin
from allauth.account.decorators import secure_admin_login
from django.urls import include, path
from accounts import orcid, privacy_views, views
from submissions.views import archive_json

admin.site.login = secure_admin_login(admin.site.login)

urlpatterns = [
    path("admin/privacy-requests/verify/", privacy_views.verify_privacy_request, name="privacy_request_verify"),
    path("admin/", admin.site.urls),
    path("accounts/privacy/", privacy_views.privacy_tools, name="account_privacy"),
    path("accounts/privacy/export/", privacy_views.download_account_data, name="account_privacy_export"),
    path("accounts/privacy/request/", privacy_views.download_privacy_request, name="account_privacy_request"),
    path("accounts/orcid/login/", orcid.oauth2_login, name="orcid_login"),
    path("accounts/orcid/login/callback/", orcid.oauth2_callback, name="orcid_callback"),
    path("accounts/", include("allauth.urls")),
    path("submissions/", include("submissions.urls")),
    path("verify/", include("repository_verification.urls")),
    path("privacy/", views.privacy, name="privacy"),
    path("health/", views.health, name="health"),
    path("submit.html", views.old_submit),
    path("data/projects.json", archive_json, name="archive_json"),
    path("assets/<path:path>", views.site_asset),
    path("", views.public_page, name="home"),
    path("<str:page>", views.public_page),
]
