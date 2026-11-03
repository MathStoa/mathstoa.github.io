from django.contrib import admin
from django.urls import include, path
from accounts import views
from submissions.views import archive_json, archive_feed

urlpatterns = [
    path("admin/", admin.site.urls),
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
