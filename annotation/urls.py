from django.urls import path
from . import views

app_name = "annotation"

urlpatterns = [
    path("", views.workspace, name="workspace"),
    path("new/", views.start, name="start"),
    path("<uuid:pk>/resume/", views.resume, name="resume"),
    # Step 1 — capture
    path("<uuid:pk>/capture/", views.capture, name="capture"),
    path("<uuid:pk>/capture/submit/", views.capture_submit, name="capture_submit"),
    # Step 2 — measurements
    path("<uuid:pk>/measurements/", views.measurements, name="measurements"),
    # Step 3 — annotate
    path("<uuid:pk>/canvas/", views.canvas, name="canvas"),
    path("<uuid:pk>/label/", views.label_particle, name="label_particle"),
    path("<uuid:pk>/particle/add/", views.add_particle, name="add_particle"),
    path("<uuid:pk>/particle/delete/", views.delete_particle, name="delete_particle"),
    # Step 4 — review & submit
    path("<uuid:pk>/review/", views.pre_submit, name="pre_submit"),
    path("<uuid:pk>/submit/", views.submit, name="submit"),
    path("<uuid:pk>/success/", views.success, name="success"),
]
