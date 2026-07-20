"""
QC reviewer flow (PRD §9):

  queue → review (split layout) → Approve | Request Rework | Reject
                                  + per-particle label override (logged)
"""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import role_required, is_qc_or_admin
from accounts.models import Role
from django.contrib.auth import get_user_model
from core.models import AuditLog, AuditAction, Commodity, Mandi
from annotation.models import (
    Submission, Particle, ParticleLabel, LABEL_COLORS, SubmissionStatus,
)

User = get_user_model()


@login_required
@role_required(is_qc_or_admin)
def queue(request):
    """Paginated review queue, oldest submitted first (PRD §9.1)."""
    qs = (Submission.objects
          .filter(submitted_at__isnull=False)
          .select_related("commodity", "assayer", "mandi")
          .order_by("submitted_at"))

    commodity = request.GET.get("commodity")
    status = request.GET.get("status")
    f_assayer = request.GET.get("assayer") or ""
    f_mandi = request.GET.get("mandi") or ""
    if commodity:
        qs = qs.filter(commodity__code=commodity)
    if f_assayer:
        qs = qs.filter(assayer_id=f_assayer)
    if f_mandi:
        qs = qs.filter(mandi_id=f_mandi)
    if status:
        qs = qs.filter(status=status)
    else:
        # Default: outstanding work only. Approved/rejected disappear; rework
        # has returned to the assayer (submitted_at cleared) so it is not here.
        qs = qs.filter(status=SubmissionStatus.PENDING_QC)

    pending_count = Submission.objects.filter(
        submitted_at__isnull=False, status=SubmissionStatus.PENDING_QC
    ).count()

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    rows = []
    for s in page.object_list:
        rows.append({
            "obj": s,
            "warning_count": len(s.warnings or []),
            "uncertain": s.uncertain_count,
            "particles": s.particle_count,
        })

    return render(request, "qc/queue.html", {
        "page": page, "rows": rows, "pending_count": pending_count,
        "commodities": Commodity.objects.filter(active=True),
        "selected_commodity": commodity or "",
        "selected_status": status or "",
        "selected_assayer": f_assayer,
        "selected_mandi": f_mandi,
        "assayers": User.objects.filter(role=Role.ASSAYER),
        "mandis": Mandi.objects.filter(active=True),
        "statuses": [(v, l) for v, l in SubmissionStatus.choices if v != SubmissionStatus.DRAFT],
    })


def _review_payload(sub):
    colors = sub.commodity.class_color_map()
    fallback = LABEL_COLORS[ParticleLabel.UNLABELED]
    return [
        {
            "id": p.id, "particle_id": p.particle_id,
            "label": p.effective_label,
            "assayer_label": p.label,
            "overridden": bool(p.qc_label_override),
            "color": colors.get(p.effective_label, fallback),
            "polygon": p.polygon, "uncertain": p.uncertain,
            "flagged_by_seg": p.flagged_by_seg, "features": p.features,
        }
        for p in sub.particles.all()
    ]


@login_required
@role_required(is_qc_or_admin)
def review(request, pk):
    sub = get_object_or_404(
        Submission.objects.select_related("commodity", "assayer", "mandi"),
        pk=pk, submitted_at__isnull=False,
    )
    dist = sub.label_distribution()
    total = sub.particle_count or 1
    classes = sub.commodity.annotation_classes()
    label_rows = [
        {"label": c["label"], "value": c["value"], "color": c["color"],
         "count": dist.get(c["value"], 0),
         "pct": round(dist.get(c["value"], 0) / total * 100, 1)}
        for c in classes
    ]
    labels = classes
    return render(request, "qc/review.html", {
        "submission": sub,
        "particles_json": json.dumps(_review_payload(sub)),
        "labels_json": json.dumps(labels),
        "label_rows": label_rows,
        "weight_rows": sub.weight_rows(),
        "total_particles": sub.particle_count,
        "crop_size": sub.capture_quality_scores.get("crop_size", [1000, 1000]),
        "can_decide": sub.status in [SubmissionStatus.PENDING_QC, SubmissionStatus.REWORK_REQUESTED],
    })


@login_required
@role_required(is_qc_or_admin)
@require_POST
def override_label(request, pk):
    """Per-particle override — always logged with reviewer + timestamp (§13.3)."""
    sub = get_object_or_404(Submission, pk=pk)
    data = json.loads(request.body or "{}")
    p = get_object_or_404(Particle, id=data.get("particle_pk"), submission=sub)
    new_label = data.get("label")
    if not sub.commodity.is_valid_label(new_label):
        return HttpResponseBadRequest("Unknown label for this commodity.")

    old = p.effective_label
    p.qc_label_override = new_label
    p.qc_overrider = request.user
    p.save(update_fields=["qc_label_override", "qc_overrider"])

    AuditLog.record(
        user=request.user, action=AuditAction.LABEL_OVERRIDE,
        entity_type="particle", entity_id=p.id,
        payload={"submission": str(sub.id), "from": old, "to": new_label},
    )
    return JsonResponse({"ok": True, "label": new_label,
                         "color": sub.commodity.class_color_map().get(
                             new_label, LABEL_COLORS[ParticleLabel.UNLABELED])})


def _decide(request, sub, status, action, message):
    sub.status = status
    sub.qc_reviewer = request.user
    sub.reviewed_at = timezone.now()
    sub.qc_notes = request.POST.get("notes", "")[:2000]
    if status == SubmissionStatus.REWORK_REQUESTED:
        sub.rework_instructions = request.POST.get("notes", "")[:2000]
        sub.submitted_at = None  # returns to the assayer's draft flow
    sub.save()
    AuditLog.record(user=request.user, action=action,
                    entity_type="submission", entity_id=sub.id,
                    payload={"notes": sub.qc_notes})
    messages.success(request, message)


@login_required
@role_required(is_qc_or_admin)
@require_POST
def approve(request, pk):
    sub = get_object_or_404(Submission, pk=pk, submitted_at__isnull=False)
    if sub.uncertain_count > 0:
        messages.error(request, "Resolve all uncertain particles before approving.")
        return redirect("qc:review", pk=sub.id)
    _decide(request, sub, SubmissionStatus.QC_APPROVED, AuditAction.QC_APPROVE,
            f"{sub.short_id} approved — eligible for the training pipeline.")
    return redirect("qc:queue")


@login_required
@role_required(is_qc_or_admin)
@require_POST
def request_rework(request, pk):
    sub = get_object_or_404(Submission, pk=pk, submitted_at__isnull=False)
    _decide(request, sub, SubmissionStatus.REWORK_REQUESTED, AuditAction.QC_REWORK,
            f"{sub.short_id} returned to the assayer for rework.")
    return redirect("qc:queue")


@login_required
@role_required(is_qc_or_admin)
@require_POST
def reject(request, pk):
    sub = get_object_or_404(Submission, pk=pk, submitted_at__isnull=False)
    _decide(request, sub, SubmissionStatus.QC_REJECTED, AuditAction.QC_REJECT,
            f"{sub.short_id} rejected — excluded from all exports.")
    return redirect("qc:queue")
