from django.urls import path
from . import views

app_name = "qc"

urlpatterns = [
    path("", views.queue, name="queue"),
    path("<uuid:pk>/review/", views.review, name="review"),
    path("<uuid:pk>/override/", views.override_label, name="override_label"),
    path("<uuid:pk>/approve/", views.approve, name="approve"),
    path("<uuid:pk>/rework/", views.request_rework, name="request_rework"),
    path("<uuid:pk>/reject/", views.reject, name="reject"),
]
