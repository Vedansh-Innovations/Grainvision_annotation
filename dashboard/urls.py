from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("users/", views.user_management, name="user_management"),
    path("users/create/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("users/<int:pk>/toggle/", views.user_toggle_active, name="user_toggle_active"),
    path("submissions/", views.submissions, name="submissions"),
    path("submissions/<uuid:pk>/", views.submission_detail, name="submission_detail"),
    path("submissions/<uuid:pk>/rework/", views.admin_rework, name="admin_rework"),
    path("submissions/<uuid:pk>/reject/", views.admin_reject, name="admin_reject"),
    path("reference/", views.reference_data, name="reference_data"),
    path("reference/mandi/create/", views.mandi_create, name="mandi_create"),
    path("reference/mandi/<int:pk>/commodities/", views.mandi_set_commodities, name="mandi_set_commodities"),
    path("reference/mandi/<int:pk>/toggle/", views.mandi_toggle, name="mandi_toggle"),
    path("reference/commodity/create/", views.commodity_create, name="commodity_create"),
    path("reference/commodity/<int:pk>/toggle/", views.commodity_toggle, name="commodity_toggle"),
    path("dataset/", views.dataset_export, name="dataset_export"),
    path("dataset/export/", views.export_coco, name="export_coco"),
    path("audit/", views.audit_log, name="audit_log"),
]
