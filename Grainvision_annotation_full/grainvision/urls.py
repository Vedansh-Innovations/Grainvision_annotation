from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.decorators import login_required
from django.urls import path, include, re_path
from django.views.static import serve

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("core.urls")),
    path("auth/", include("accounts.urls")),
    path("annotate/", include("annotation.urls")),
    path("qc/", include("qc.urls")),
    path("admin/", include("dashboard.urls")),
    path("api/ml/", include("ml.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif not settings.USE_S3 and settings.SERVE_MEDIA:
    # Production media serving for local-filesystem / Azure Files setups.
    # Login-required because grain plate images are sensitive. When media is on
    # object storage (USE_S3), files are served by the storage backend instead.
    media_prefix = settings.MEDIA_URL.lstrip("/")
    urlpatterns += [
        re_path(
            rf"^{media_prefix}(?P<path>.*)$",
            login_required(serve),
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
