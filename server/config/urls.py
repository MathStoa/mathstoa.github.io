from django.contrib import admin
from allauth.account.decorators import secure_admin_login
from django.urls import include, path
from accounts import orcid, views
from submissions.views import archive_json, archive_feed

admin.site.login = secure_admin_login(admin.site.login)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/orcid/login/", orcid.oauth2_login, name="orcid_login"),
    path("accounts/orcid/login/callback/", orcid.oauth2_callback, name="orcid_callback"),
    path("accounts/", include("allauth.urls")),
    path("submissions/", include("submissions.urls")),
    path("verify/", include("repository_verification.urls")),
    path("privacy/", views.privacy, name="privacy"),
    path("health/", views.health, name="health"),
    path("submit.html", views.old_submit),
    path("data/projects.json", archive_json, name="archive_json"),
    path("feed.xml", archive_feed, name="archive_feed"),
    path("assets/<path:path>", views.site_asset),
    path("", views.public_page, name="home"),
    path("<str:page>", views.public_page),
]
