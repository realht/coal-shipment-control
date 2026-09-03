from django.urls import path
from . import views

app_name = "imports"

urlpatterns = [
    path("", views.ImportIndexView.as_view(), name="index"),
    path("upload/", views.ImportUploadView.as_view(), name="upload"),
    path("preview/", views.ImportPreviewView.as_view(), name="preview"),
    path("result/<int:pk>/", views.ImportResultView.as_view(), name="result"),
    path("log/", views.ImportLogView.as_view(), name="log"),
]
