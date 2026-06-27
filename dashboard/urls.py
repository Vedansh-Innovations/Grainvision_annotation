from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("users/", views.user_management, name="user_management"),
    path("users/create/", views.user_create, name="user_create"),
    path("users/<int:pk>/toggle/", views.user_toggle_active, name="user_toggle_active"),
    path("reference/", views.reference_data, name="reference_data"),
    path("reference/mandi/create/", views.mandi_create, name="mandi_create"),
    path("reference/mandi/<int:pk>/toggle/", views.mandi_toggle, name="mandi_toggle"),
    path("reference/commodity/create/", views.commodity_create, name="commodity_create"),
    path("reference/commodity/<int:pk>/toggle/", views.commodity_toggle, name="commodity_toggle"),
    path("dataset/", views.dataset_export, name="dataset_export"),
    path("dataset/export/", views.export_coco, name="export_coco"),
    path("audit/", views.audit_log, name="audit_log"),
]
