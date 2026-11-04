from django.urls import path

from . import views

app_name = "submissions"
urlpatterns = [
    path("", views.submission_list, name="list"),
    path("new/", views.create, name="create"),
    path("<uuid:pk>/", views.detail, name="detail"),
    path("<uuid:pk>/edit/", views.edit, name="edit"),
    path("<uuid:pk>/delete/", views.delete, name="delete"),
    path("<uuid:pk>/submit/", views.submit, name="submit"),
]
