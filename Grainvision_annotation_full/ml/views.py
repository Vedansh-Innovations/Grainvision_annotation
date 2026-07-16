from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import sam2_loader


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def segmentation_health(request):
    """Report segmentation engine status (PRD §6 / ops visibility)."""
    available = sam2_loader.available()
    # Touch the generator so a load error (if any) is populated.
    if available:
        sam2_loader.get_mask_generator()
    return Response({
        "sam2_enabled": settings.SAM2_ENABLED,
        "sam2_required": settings.SAM2_REQUIRED,
        "sam2_available": available,
        "device": settings.SAM2_DEVICE,
        "points_per_side": settings.SAM2_POINTS_PER_SIDE,
        "engine": "sam2" if available else ("unavailable" if settings.SAM2_REQUIRED else "watershed"),
        "load_error": sam2_loader.load_error(),
    })
