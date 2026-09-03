from django.urls import path
from . import views

app_name = "auto"

urlpatterns = [
    path("", views.AutoShipmentListView.as_view(), name="list"),
    path("export/", views.AutoShipmentExportView.as_view(), name="export"),
    path("export/selected/", views.AutoShipmentExportSelectedView.as_view(), name="export_selected"),
    path("new/", views.AutoShipmentCreateView.as_view(), name="create"),
    path("<int:pk>/", views.AutoShipmentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.AutoShipmentUpdateView.as_view(), name="update"),
    path("<int:pk>/delete/", views.AutoShipmentDeleteView.as_view(), name="delete"),
    path("deleted/", views.AutoShipmentDeletedListView.as_view(), name="deleted"),
    path("deleted/<int:pk>/restore/", views.AutoShipmentRestoreView.as_view(), name="restore"),
    path("filter-values/<str:field>/", views.AutoFilterValuesView.as_view(), name="filter_values"),
]
