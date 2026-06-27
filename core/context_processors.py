def platform_context(request):
    """Globals available to every template."""
    ctx = {
        "PLATFORM_NAME": "GrainVision AI",
        "PLATFORM_SUBTITLE": "Data Annotation Platform",
        "CLIENT_NAME": "Prayathi Techno Solutions",
    }
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        try:
            from annotation.models import Submission, SubmissionStatus
            if user.is_qc or user.is_platform_admin:
                ctx["pending_count"] = Submission.objects.filter(
                    submitted_at__isnull=False, status=SubmissionStatus.PENDING_QC
                ).count()
            if user.is_assayer:
                ctx["rework_count"] = Submission.objects.filter(
                    assayer=user, status=SubmissionStatus.REWORK_REQUESTED
                ).count()
        except Exception:
            pass
    return ctx
