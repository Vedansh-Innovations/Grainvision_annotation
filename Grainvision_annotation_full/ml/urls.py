from django.urls import path
from . import views

app_name = "ml"

urlpatterns = [
    path("health/", views.segmentation_health, name="health"),
]
