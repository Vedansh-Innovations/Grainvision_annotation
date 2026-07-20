"""
Assayer flow (reordered per field feedback):

  workspace ─▶ new sample (commodity + mandi)
            └▶ STEP 1 capture (photo, auto)  ── saves immediately
               STEP 2 measurements           ── then "annotate now" OR "save for later"
               STEP 3 annotate (canvas)
               STEP 4 review ─▶ submit ─▶ QC

A draft is saved after every step, so an assayer can leave and resume later from
the workspace. Rework returned by QC reappears in the workspace for re-annotation.
"""
import io
import json
from decimal import Decimal, InvalidOperation

import cv2
import numpy as np
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from PIL import Image

from accounts.permissions import role_required, is_assayer
from core.models import AuditLog, AuditAction, Commodity
from ml import segmentation

from .models import (
    Submission, Particle, ParticleLabel, LABEL_COLORS,
    SubmissionStatus, CaptureMode, ParticleOrigin,
)
from .services import (
    validate_measurements, segmentation_flags, cross_validate,
)


def _owned(request, sub):
    return sub.assayer_id == request.user.id


def _editable(sub):
    """A sample is editable by the assayer while it is a draft or in rework."""
    return sub.submitted_at is None  # draft or returned-for-rework


# ── Assayer workspace (landing) ───────────────────────────────────
@login_required
@role_required(is_assayer)
def workspace(request):
    mine = Submission.objects.filter(assayer=request.user).select_related("commodity", "mandi")
    todo = [s for s in mine if s.submitted_at is None]            # drafts + rework
    todo.sort(key=lambda s: (s.status != SubmissionStatus.REWORK_REQUESTED, -s.created_at.timestamp()))
    submitted = [s for s in mine if s.submitted_at is not None][:12]
    return render(request, "annotation/workspace.html", {
        "todo": todo,
        "submitted": submitted,
        "rework_count": sum(1 for s in todo if s.status == SubmissionStatus.REWORK_REQUESTED),
    })


# ── New sample: choose commodity + mandi, then go to capture ──────
@login_required
@role_required(is_assayer)
@require_http_methods(["GET", "POST"])
def start(request):
    import json as _json
    mandis = request.user.allowed_mandis()
    commodities = request.user.allowed_commodities()
    # mandi -> its commodity ids, so the form can narrow the list per mandi.
    mandi_commodities = {
        str(m.id): list(m.commodities.filter(active=True).values_list("id", flat=True))
        for m in mandis
    }

    if request.method == "POST":
        commodity = commodities.filter(id=request.POST.get("commodity")).first()
        mandi = mandis.filter(id=request.POST.get("mandi")).first()
        if not commodity or not mandi:
            messages.error(request, "Select a valid commodity and mandi to begin.")
            return render(request, "annotation/new_sample.html",
                          {"mandis": mandis, "commodities": commodities,
                           "mandi_commodities": _json.dumps(mandi_commodities)})

        next_num = (Submission.objects.filter(assayer=request.user)
                    .order_by("-sample_number").values_list("sample_number", flat=True).first() or 0) + 1
        sub = Submission.objects.create(
            assayer=request.user, commodity=commodity, mandi=mandi,
            sample_number=next_num, status=SubmissionStatus.DRAFT,
        )
        return redirect("annotation:capture", pk=sub.id)

    if not mandis.exists():
        messages.error(request, "No mandi is assigned to your account yet. Ask an admin to assign one.")
    return render(request, "annotation/new_sample.html",
                  {"mandis": mandis, "commodities": commodities,
                   "mandi_commodities": _json.dumps(mandi_commodities)})


# ── Resume a saved draft at the right step ────────────────────────
@login_required
@role_required(is_assayer)
def resume(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        messages.error(request, "That sample is not available.")
        return redirect("annotation:workspace")
    # Rework always reopens on the annotation canvas so the assayer can revise.
    if sub.needs_rework:
        if not sub.has_capture:
            return redirect("annotation:capture", pk=sub.id)
        if not sub.measurements_done:
            return redirect("annotation:measurements", pk=sub.id)
        return redirect("annotation:canvas", pk=sub.id)
    step = sub.resume_step
    return redirect({
        1: "annotation:capture",
        2: "annotation:measurements",
        3: "annotation:canvas",
        4: "annotation:pre_submit",
    }[step], pk=sub.id)


# ── STEP 1: Auto-capture (PRD §5) ─────────────────────────────────
@login_required
@role_required(is_assayer)
def capture(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        messages.error(request, "That sample is not available for capture.")
        return redirect("annotation:workspace")
    return render(request, "annotation/capture.html", {"submission": sub})


@login_required
@role_required(is_assayer)
@require_POST
def capture_submit(request, pk):
    """Auto-captured frame → segmentation → particles. Manual capture rejected (§5.1)."""
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        return HttpResponseBadRequest("Sample not available.")

    image_file = request.FILES.get("image")
    if not image_file:
        return HttpResponseBadRequest("No image provided.")

    try:
        scores = json.loads(request.POST.get("quality_scores", "{}"))
    except json.JSONDecodeError:
        scores = {}

    if scores.get("capture_mode") == "manual" or request.POST.get("manual") == "1":
        return HttpResponseBadRequest("Manual capture is not permitted.")

    raw = image_file.read()
    pil = Image.open(io.BytesIO(raw)).convert("RGB")
    bgr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    # The current capture page crops to the guide client-side and flags it;
    # skip server plate re-detection for those uploads (see isolate_plate).
    pre_cropped = bool(scores.get("exif", {}).get("cropped_to_guide"))
    result = segmentation.segment_image(bgr, sub.commodity, pre_cropped=pre_cropped)

    crop_rgb = cv2.cvtColor(result["crop_bgr"], cv2.COLOR_BGR2RGB)
    crop_buf = io.BytesIO()
    Image.fromarray(crop_rgb).save(crop_buf, format="JPEG", quality=95)

    with transaction.atomic():
        sub.raw_image.save(f"{sub.short_id}.jpg", ContentFile(raw), save=False)
        sub.crop_image.save(f"{sub.short_id}_crop.jpg", ContentFile(crop_buf.getvalue()), save=False)
        sub.capture_mode = CaptureMode.AUTO
        sub.exif_json = scores.get("exif", {"captured": True, "color_space": "sRGB"})
        sub.capture_quality_scores = {
            **scores,
            "plate": result["plate"],
            "crop_size": result["crop_size"],
            "engine": result["engine"],
            "dark_fraction": result["dark_fraction"],
            "scale": result.get("scale", {}),
            "glare": scores.get("glare", False),
        }
        sub.warnings = segmentation_flags(
            particle_count=len(result["particles"]),
            merge_flagged_count=result["merge_flagged_count"],
            dark_fraction=result["dark_fraction"],
            expected_min=sub.commodity.expected_min_count,
        )
        sub.save()

        sub.particles.all().delete()
        for idx, p in enumerate(result["particles"], start=1):
            Particle.objects.create(
                submission=sub, particle_id=idx,
                label=ParticleLabel.UNLABELED,
                polygon=p["polygon"], features=p["features"],
                origin=ParticleOrigin.AUTO, flagged_by_seg=p["flagged_by_seg"],
            )

    # Photo saved → proceed to measurements (step 2).
    return JsonResponse({"ok": True, "redirect": f"/annotate/{sub.id}/measurements/"})


# ── STEP 2: Physiochemical measurements (PRD §3) ──────────────────
@login_required
@role_required(is_assayer)
@require_http_methods(["GET", "POST"])
def measurements(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        messages.error(request, "That sample is not available.")
        return redirect("annotation:workspace")
    if not sub.has_capture:
        messages.error(request, "Capture the grain photo first.")
        return redirect("annotation:capture", pk=sub.id)

    classes = sub.commodity.annotation_classes()

    if request.method == "POST":
        try:
            total = Decimal(request.POST.get("total_weight_g", "")).quantize(Decimal("0.01"))
            weights = {
                c["value"]: Decimal(request.POST.get(f"weight_{c['value']}", "")).quantize(Decimal("0.01"))
                for c in classes
            }
        except (InvalidOperation, TypeError):
            messages.error(request, "The total and every class weight must be valid numbers to two decimals.")
            return render(request, "annotation/measurements.html",
                          {"submission": sub, "classes": classes, "post": request.POST})

        ok, errors, _zero = validate_measurements(
            total, weights, {c["value"]: c["label"] for c in classes})
        if not ok or errors:
            for e in errors:
                messages.error(request, e)
            return render(request, "annotation/measurements.html",
                          {"submission": sub, "classes": classes, "post": request.POST})

        sub.total_weight_g = total
        sub.class_weights = {v: str(w) for v, w in weights.items()}
        # Mirror the three legacy defect columns so old reports keep working.
        sub.foreign_matter_g = weights.get("foreign")
        sub.fungal_grains_g = weights.get("fungal")
        sub.immature_grains_g = weights.get("immature")
        sub.measurements_done = True
        sub.save(update_fields=[
            "total_weight_g", "class_weights", "foreign_matter_g",
            "fungal_grains_g", "immature_grains_g", "measurements_done", "updated_at",
        ])

        if request.POST.get("action") == "later":
            messages.success(request, f"{sub.short_id} saved. You can annotate it later from your workspace.")
            return redirect("annotation:workspace")
        return redirect("annotation:canvas", pk=sub.id)

    # Pre-fill if returning
    post = {}
    if sub.measurements_done:
        post = {"total_weight_g": sub.total_weight_g}
        for c in classes:
            post[f"weight_{c['value']}"] = sub.class_weight_g(c["value"])
    return render(request, "annotation/measurements.html",
                  {"submission": sub, "classes": classes, "post": post})


# ── STEP 3: Annotation canvas (PRD §7) ────────────────────────────
def _particles_payload(sub):
    colors = sub.commodity.class_color_map()
    fallback = LABEL_COLORS[ParticleLabel.UNLABELED]
    out = []
    for p in sub.particles.all():
        eff = p.effective_label
        out.append({
            "id": p.id, "particle_id": p.particle_id,
            "label": eff,                       # show QC's override if present
            "assayer_label": p.label,
            "qc_overridden": bool(p.qc_label_override),
            "color": colors.get(eff, fallback),
            "polygon": p.polygon, "origin": p.origin, "uncertain": p.uncertain,
            "flagged_by_seg": p.flagged_by_seg, "features": p.features,
        })
    return out


@login_required
@role_required(is_assayer)
def canvas(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        messages.error(request, "That sample is not available for annotation.")
        return redirect("annotation:workspace")
    if not sub.has_capture:
        return redirect("annotation:capture", pk=sub.id)
    if not sub.measurements_done:
        return redirect("annotation:measurements", pk=sub.id)

    labels = sub.commodity.annotation_classes()
    return render(request, "annotation/canvas.html", {
        "submission": sub,
        "particles_json": json.dumps(_particles_payload(sub)),
        "labels": labels,
        "labels_json": json.dumps(labels),
        "crop_size": sub.capture_quality_scores.get("crop_size", [1000, 1000]),
        "rework_instructions": sub.rework_instructions if sub.needs_rework else "",
    })


@login_required
@role_required(is_assayer)
@require_POST
def label_particle(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        return HttpResponseBadRequest("Not editable.")
    data = json.loads(request.body or "{}")
    p = get_object_or_404(Particle, id=data.get("particle_pk"), submission=sub)
    label = data.get("label")
    if label == "uncertain":
        p.uncertain = True
    else:
        if not sub.commodity.is_valid_label(label):
            return HttpResponseBadRequest("Unknown label for this commodity.")
        p.label = label
        p.uncertain = False
    # An assayer's fresh choice supersedes any QC override (keeps them in sync).
    fields = ["label", "uncertain"]
    if p.qc_label_override:
        p.qc_label_override = ""
        p.qc_overrider = None
        fields += ["qc_label_override", "qc_overrider"]
    p.save(update_fields=fields)
    return JsonResponse({
        "ok": True, "label": p.label, "uncertain": p.uncertain,
        "color": sub.commodity.class_color_map().get(
            p.label, LABEL_COLORS[ParticleLabel.UNLABELED]),
        "remaining": sub.unlabeled_count,
    })


@login_required
@role_required(is_assayer)
@require_POST
def add_particle(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        return HttpResponseBadRequest("Not editable.")
    data = json.loads(request.body or "{}")
    polygon = data.get("polygon")
    if not polygon or len(polygon) < 3:
        return HttpResponseBadRequest("Polygon needs at least 3 points.")
    label = data.get("label")
    if not sub.commodity.is_valid_label(label):
        label = ParticleLabel.UNLABELED

    snapped = False
    if data.get("snap") and sub.crop_image:
        try:
            import cv2
            import numpy as np
            from ml.snap import snap_polygon
            with sub.crop_image.open("rb") as f:
                arr = np.frombuffer(f.read(), np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            min_a = getattr(sub.commodity, "min_particle_area_px", 20) or 20
            max_a = getattr(sub.commodity, "max_particle_area_px", 10**9) or 10**9
            snap = snap_polygon(bgr, polygon, min_a, max_a)
            if snap and len(snap) >= 3:
                polygon = snap
                snapped = True
        except Exception:
            pass

    next_id = (sub.particles.order_by("-particle_id")
               .values_list("particle_id", flat=True).first() or 0) + 1
    p = Particle.objects.create(
        submission=sub, particle_id=next_id, polygon=polygon,
        label=label, origin=ParticleOrigin.USER, boundary_edited=True,
    )
    return JsonResponse({
        "ok": True, "id": p.id, "particle_id": p.particle_id,
        "label": p.label,
        "color": sub.commodity.class_color_map().get(
            p.label, LABEL_COLORS[ParticleLabel.UNLABELED]),
        "polygon": polygon, "snapped": snapped,
        "remaining": sub.unlabeled_count,
    })


@login_required
@role_required(is_assayer)
@require_POST
def edit_particle(request, pk):
    """Update a particle's polygon geometry (vertex drag / box-ellipse resize)."""
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        return HttpResponseBadRequest("Not editable.")
    data = json.loads(request.body or "{}")
    polygon = data.get("polygon")
    if not polygon or len(polygon) < 3:
        return HttpResponseBadRequest("Polygon needs at least 3 points.")
    p = get_object_or_404(Particle, id=data.get("particle_pk"), submission=sub)
    p.polygon = polygon
    p.boundary_edited = True
    p.save(update_fields=["polygon", "boundary_edited"])
    return JsonResponse({"ok": True})


@login_required
@role_required(is_assayer)
@require_POST
def delete_particle(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        return HttpResponseBadRequest("Not editable.")
    data = json.loads(request.body or "{}")
    Particle.objects.filter(id=data.get("particle_pk"), submission=sub).delete()
    return JsonResponse({"ok": True, "remaining": sub.unlabeled_count})


# ── STEP 4: Pre-submit review (PRD §8.1) ──────────────────────────
@login_required
@role_required(is_assayer)
def pre_submit(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        messages.error(request, "That sample is not available.")
        return redirect("annotation:workspace")
    if not sub.measurements_done:
        return redirect("annotation:measurements", pk=sub.id)

    dist = sub.label_distribution()
    total = sub.particle_count or 1
    colors = sub.commodity.class_color_map()
    fallback = LABEL_COLORS[ParticleLabel.UNLABELED]
    label_rows = [
        {"label": c["label"], "value": c["value"], "color": c["color"],
         "count": dist.get(c["value"], 0),
         "pct": round(dist.get(c["value"], 0) / total * 100, 1)}
        for c in sub.commodity.annotation_classes()
    ]
    return render(request, "annotation/pre_submit.html", {
        "submission": sub,
        "label_rows": label_rows,
        "weight_rows": sub.weight_rows(),
        "uncertain": sub.uncertain_count,
        "unlabeled": sub.unlabeled_count,
        "cross_validation": cross_validate(sub),
        "particles_json": json.dumps([
            {"polygon": p.polygon,
             "color": colors.get(p.effective_label, fallback),
             "unlabeled": p.effective_label == ParticleLabel.UNLABELED}
            for p in sub.particles.all()
        ]),
        "crop_size": sub.capture_quality_scores.get("crop_size", [1000, 1000]),
    })


@login_required
@role_required(is_assayer)
@require_POST
def submit(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub) or not _editable(sub):
        return HttpResponseBadRequest("Not submittable.")
    if not sub.measurements_done:
        messages.error(request, "Enter measurements before submitting.")
        return redirect("annotation:measurements", pk=sub.id)
    if sub.unlabeled_count > 0:
        messages.error(request, f"{sub.unlabeled_count} particle(s) still unlabeled.")
        return redirect("annotation:canvas", pk=sub.id)

    was_rework = sub.needs_rework
    sub.warnings = (sub.warnings or []) + cross_validate(sub)
    sub.submitted_at = timezone.now()
    sub.status = SubmissionStatus.PENDING_QC
    sub.save(update_fields=["warnings", "submitted_at", "status", "updated_at"])

    AuditLog.record(
        user=request.user, action=AuditAction.SUBMIT,
        entity_type="submission", entity_id=sub.id,
        payload={"particles": sub.particle_count, "commodity": sub.commodity.code,
                 "resubmitted": was_rework},
    )
    return redirect("annotation:success", pk=sub.id)


@login_required
@role_required(is_assayer)
def success(request, pk):
    sub = get_object_or_404(Submission, pk=pk)
    if not _owned(request, sub):
        return redirect("annotation:workspace")
    return render(request, "annotation/success.html", {"submission": sub})
