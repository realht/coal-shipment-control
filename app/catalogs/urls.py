from django.urls import path
from . import views

app_name = "catalogs"

urlpatterns = [
    path("", views.catalog_list, name="list"),
    path("value/<int:pk>/toggle/", views.catalog_value_toggle, name="toggle"),
    path("value/<int:pk>/delete/", views.catalog_value_delete, name="delete"),
    path("value/<int:pk>/edit/", views.catalog_value_edit, name="edit"),
    path("<path:catalog_type>/add/", views.catalog_value_add, name="add"),
    path("<path:catalog_type>/", views.catalog_values, name="values"),
]
