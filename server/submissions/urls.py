from django.urls import path

from . import views

app_name = "submissions"
urlpatterns = [
    path("", views.submission_list, name="list"),
    path("new/", views.create, name="create"),
    path("invitations/<str:token>/", views.accept_invitation, name="accept_invitation"),
    path("<uuid:pk>/", views.detail, name="detail"),
    path("<uuid:pk>/edit/", views.edit, name="edit"),
    path("<uuid:pk>/submit/", views.submit, name="submit"),
    path("<uuid:pk>/invite/", views.invite, name="invite"),
    path("<uuid:pk>/invitations/<uuid:invitation_id>/revoke/", views.revoke_invitation, name="revoke_invitation"),
    path("<uuid:pk>/editors/<int:user_id>/remove/", views.remove_editor, name="remove_editor"),
]
