from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.UserListView.as_view(), name="list"),
    path("new/", views.UserCreateView.as_view(), name="create"),
    path("<int:pk>/edit/", views.UserUpdateView.as_view(), name="update"),
    path("<int:pk>/password/", views.UserPasswordView.as_view(), name="password"),
    path("<int:pk>/deactivate/", views.UserDeactivateView.as_view(), name="deactivate"),
    path("<int:pk>/activate/", views.UserActivateView.as_view(), name="activate"),
]
