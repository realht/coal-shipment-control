from django.urls import path
from . import views

app_name = "documents"

urlpatterns = [
    path("<str:shipment_type>/<int:pk>/upload/", views.DocumentUploadView.as_view(), name="upload"),
    path("<int:pk>/edit/", views.DocumentEditView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.DocumentDeleteView.as_view(), name="delete"),
    path("<int:pk>/serve/", views.DocumentServeView.as_view(), name="serve"),
    path("<int:pk>/view/", views.DocumentServeView.as_view(), {"mode": "view"}, name="view"),
    path("<int:pk>/download/", views.DocumentServeView.as_view(), {"mode": "download"}, name="download"),
]
