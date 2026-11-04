from django.urls import path

from . import views

app_name = "verification"
urlpatterns = [
    path("<uuid:pk>/", views.overview, name="overview"),
    path("<uuid:pk>/file/instructions/", views.file_overview, name="file_overview"),
    path("<uuid:pk>/file/", views.issue_file, name="issue_file"),
    path("<uuid:pk>/file/check/", views.verify_file, name="verify_file"),
    path("<uuid:pk>/github/instructions/", views.github_overview, name="github_overview"),
    path("<uuid:pk>/github/", views.github_start, name="github_start"),
]
