from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", core_views.index, name="index"),
    path("duplicates/", core_views.duplicates, name="duplicates"),
    path("auto/", include("shipments_auto.urls", namespace="auto")),
    path("rail/", include("shipments_rail.urls", namespace="rail")),
    path("documents/", include("documents.urls", namespace="documents")),
    path("audit/", include("audit.urls", namespace="audit")),
    path("users/", include("accounts.urls", namespace="accounts")),
    path("imports/", include("imports.urls", namespace="imports")),
    path("", include("core.urls", namespace="core")),
    path("settings/catalogs/", include("catalogs.urls", namespace="catalogs")),
]
