# users/urls.py
from django.urls import path
from . import views

app_name = "users"

urlpatterns = [
    path("profile/", views.profile_update, name="profile_update"),
]
