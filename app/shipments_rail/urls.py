from django.urls import path
from . import views

app_name = "rail"

urlpatterns = [
    path("", views.RailShipmentListView.as_view(), name="list"),
    path("export/", views.RailShipmentExportView.as_view(), name="export"),
    path("export/selected/", views.RailShipmentExportSelectedView.as_view(), name="export_selected"),
    path("new/", views.RailShipmentCreateView.as_view(), name="create"),
    path("<int:pk>/", views.RailShipmentDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.RailShipmentUpdateView.as_view(), name="update"),
    path("<int:pk>/delete/", views.RailShipmentDeleteView.as_view(), name="delete"),
    path("deleted/", views.RailShipmentDeletedListView.as_view(), name="deleted"),
    path("deleted/<int:pk>/restore/", views.RailShipmentRestoreView.as_view(), name="restore"),
    path("filter-values/<str:field>/", views.RailFilterValuesView.as_view(), name="filter_values"),
]
