from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


@login_required
def home(request):
    """Route each role to its primary surface (PRD flow)."""
    u = request.user
    if u.is_platform_admin:
        return redirect("dashboard:overview")
    if u.is_qc:
        return redirect("qc:queue")
    if u.is_ml_engineer:
        return redirect("dashboard:dataset_export")
    return redirect("annotation:workspace")  # assayer default
