"""
Admin & ML-engineer surfaces (PRD §10, §12):

  overview          — platform health, label distribution, dataset progress, assayer table
  user_management   — accounts CRUD-lite
  dataset_export    — COCO export with eligibility + dataset-readiness gates
  audit_log         — append-only trail viewer
"""
import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from accounts.permissions import role_required, is_admin, is_ml_or_admin
from core.models import AuditLog, AuditAction, Commodity, Mandi
from annotation.models import (
    Submission, Particle, ParticleLabel, LABEL_COLORS, SubmissionStatus,
)
from ml import export as coco_export

User = get_user_model()

APPROVAL_THRESHOLD = 88.0      # PRD §12.1
MINORITY_THRESHOLD = 10.0      # PRD §10.1


@login_required
@role_required(is_admin)
def overview(request):
    submitted = Submission.objects.filter(submitted_at__isnull=False)
    total_submissions = submitted.count()
    approved = submitted.filter(status=SubmissionStatus.QC_APPROVED)
    reviewed = submitted.filter(status__in=[
        SubmissionStatus.QC_APPROVED, SubmissionStatus.QC_REJECTED,
    ])
    approval_rate = round(approved.count() / reviewed.count() * 100, 1) if reviewed.count() else 0.0
    pending_qc = submitted.filter(status=SubmissionStatus.PENDING_QC).count()
    total_particles = Particle.objects.filter(
        submission__status=SubmissionStatus.QC_APPROVED
    ).count()

    # Label distribution across approved data.
    dist = {l.value: 0 for l in ParticleLabel if l != ParticleLabel.UNLABELED}
    rows = (Particle.objects
            .filter(submission__status=SubmissionStatus.QC_APPROVED)
            .exclude(label=ParticleLabel.UNLABELED)
            .values("label").annotate(n=Count("id")))
    grand = sum(r["n"] for r in rows) or 1
    for r in rows:
        if r["label"] in dist:
            dist[r["label"]] = r["n"]
    label_dist = [
        {"label": ParticleLabel(k).label, "value": k, "color": LABEL_COLORS[ParticleLabel(k)],
         "pct": round(v / grand * 100, 1), "minority": (v / grand * 100) < MINORITY_THRESHOLD}
        for k, v in dist.items()
    ]

    # Dataset progress per commodity.
    progress = []
    for c in Commodity.objects.filter(active=True):
        n = approved.filter(commodity=c).count()
        progress.append({
            "name": c.name, "approved": n, "target": c.target_samples,
            "pct": min(100, round(n / c.target_samples * 100)) if c.target_samples else 0,
        })

    # Assayer performance this week.
    week_ago = timezone.now() - timezone.timedelta(days=7)
    assayer_rows = []
    for u in User.objects.filter(role=Role.ASSAYER):
        week = Submission.objects.filter(
            assayer=u, submitted_at__gte=week_ago, submitted_at__isnull=False)
        n = week.count()
        if n == 0:
            continue
        decided = week.filter(status__in=[SubmissionStatus.QC_APPROVED, SubmissionStatus.QC_REJECTED])
        appr = week.filter(status=SubmissionStatus.QC_APPROVED).count()
        appr_rate = round(appr / decided.count() * 100) if decided.count() else 0
        uncertain = sum(s.uncertain_count for s in week)
        total_p = sum(s.particle_count for s in week) or 1
        uncertain_rate = round(uncertain / total_p * 100, 1)
        avg_particles = round(total_p / n)
        assayer_rows.append({
            "name": u.get_full_name() or u.username, "samples": n,
            "approval_rate": appr_rate, "uncertain_rate": uncertain_rate,
            "avg_particles": avg_particles,
            "flagged": appr_rate < APPROVAL_THRESHOLD or uncertain_rate > 5.0,
        })

    return render(request, "dashboard/overview.html", {
        "total_submissions": total_submissions,
        "approval_rate": approval_rate,
        "pending_qc": pending_qc,
        "total_particles": total_particles,
        "label_dist": label_dist,
        "progress": progress,
        "assayer_rows": assayer_rows,
        "minority_threshold": MINORITY_THRESHOLD,
    })


@login_required
@role_required(is_admin)
def user_management(request):
    users = User.objects.prefetch_related("mandis").all()
    rows = []
    for u in users:
        rows.append({
            "obj": u,
            "submissions": Submission.objects.filter(assayer=u, submitted_at__isnull=False).count(),
            "mandi_names": ", ".join(m.name for m in u.mandis.all()) or "All locations",
        })
    return render(request, "dashboard/user_management.html", {
        "rows": rows,
        "roles": Role.choices,
        "mandis": Mandi.objects.filter(active=True),
        "active_count": users.filter(is_active=True).count(),
    })


@login_required
@role_required(is_admin)
@require_POST
def user_create(request):
    username = request.POST.get("username", "").strip()
    email = request.POST.get("email", "").strip()
    name = request.POST.get("name", "").strip()
    role = request.POST.get("role", Role.ASSAYER)
    password = request.POST.get("password", "")

    if not username or not password:
        messages.error(request, "Username and a temporary password are required.")
        return redirect("dashboard:user_management")
    if User.objects.filter(username__iexact=username).exists():
        messages.error(request, "That username already exists.")
        return redirect("dashboard:user_management")

    first, _, last = name.partition(" ")
    u = User.objects.create_user(
        username=username, email=email, password=password,
        first_name=first, last_name=last, role=role,
    )
    for mid in request.POST.getlist("mandis"):
        u.mandis.add(mid)
    AuditLog.record(user=request.user, action=AuditAction.USER_CREATE,
                    entity_type="user", entity_id=u.id, payload={"role": role})
    messages.success(request, f"User {u} created.")
    return redirect("dashboard:user_management")


@login_required
@role_required(is_admin)
@require_POST
def user_toggle_active(request, pk):
    u = get_object_or_404(User, pk=pk)
    u.is_active = not u.is_active
    u.save(update_fields=["is_active"])
    AuditLog.record(user=request.user, action=AuditAction.USER_UPDATE,
                    entity_type="user", entity_id=u.id,
                    payload={"is_active": u.is_active})
    messages.success(request, f"{u} {'activated' if u.is_active else 'deactivated'}.")
    return redirect("dashboard:user_management")


@login_required
@role_required(is_ml_or_admin)
def dataset_export(request):
    """Dataset readiness per commodity + COCO export (PRD §12)."""
    readiness = []
    for c in Commodity.objects.filter(active=True):
        approved = Submission.objects.filter(commodity=c, status=SubmissionStatus.QC_APPROVED)
        n = approved.count()
        foreign = approved.filter(particles__label=ParticleLabel.FOREIGN).distinct().count()
        fungal = approved.filter(particles__label=ParticleLabel.FUNGAL).distinct().count()
        assayers = approved.values("assayer").distinct().count()
        mandi_n = approved.values("mandi").distinct().count()
        gates = {
            "Approved ≥ 500": (n, n >= 500),
            "Foreign ≥ 200": (foreign, foreign >= 200),
            "Fungal ≥ 150": (fungal, fungal >= 150),
            "Assayers ≥ 5": (assayers, assayers >= 5),
            "Mandis ≥ 3": (mandi_n, mandi_n >= 3),
        }
        readiness.append({
            "commodity": c, "n": n,
            "gates": gates,
            "ready": all(passed for _, passed in gates.values()),
        })
    return render(request, "dashboard/dataset_export.html", {"readiness": readiness})


@login_required
@role_required(is_ml_or_admin)
def export_coco(request):
    code = request.GET.get("commodity")
    commodity = Commodity.objects.filter(code=code).first() if code else None
    coco, included = coco_export.build_coco(commodity)
    valid, errors = coco_export.validate_coco(coco)
    if not valid:
        return HttpResponse("Export failed schema validation:\n" + "\n".join(errors),
                            content_type="text/plain", status=500)

    AuditLog.record(user=request.user, action=AuditAction.EXPORT,
                    entity_type="dataset", entity_id=code or "all",
                    payload={"submissions": included, "annotations": len(coco["annotations"])})

    fname = f"grainvision_{code or 'all'}_coco.json"
    resp = HttpResponse(json.dumps(coco, indent=2), content_type="application/json")
    resp["Content-Disposition"] = f'attachment; filename="{fname}"'
    return resp


@login_required
@role_required(is_admin)
def audit_log(request):
    entries = AuditLog.objects.select_related("user")[:300]
    return render(request, "dashboard/audit_log.html", {"entries": entries})


# ── Mandi & Commodity management (reference data) ─────────────────
@login_required
@role_required(is_admin)
def reference_data(request):
    mandis = Mandi.objects.all().order_by("-active", "name")
    commodities = Commodity.objects.all().order_by("-active", "name")
    rows = []
    for c in commodities:
        approved = Submission.objects.filter(commodity=c, status=SubmissionStatus.QC_APPROVED).count()
        rows.append({"obj": c, "approved": approved})
    return render(request, "dashboard/reference_data.html", {
        "mandis": mandis,
        "commodities": rows,
    })


@login_required
@role_required(is_admin)
@require_POST
def mandi_create(request):
    name = (request.POST.get("name") or "").strip()
    district = (request.POST.get("district") or "").strip()
    state = (request.POST.get("state") or "").strip()
    if not (name and district and state):
        messages.error(request, "Mandi name, district and state are all required.")
        return redirect("dashboard:reference_data")
    m, created = Mandi.objects.get_or_create(
        name=name, defaults={"district": district, "state": state}
    )
    if not created:
        messages.error(request, f"A mandi named “{name}” already exists.")
    else:
        AuditLog.record(user=request.user, action=AuditAction.USER_CREATE,
                        entity_type="mandi", entity_id=m.id,
                        payload={"name": name, "state": state})
        messages.success(request, f"Mandi “{name}” added.")
    return redirect("dashboard:reference_data")


@login_required
@role_required(is_admin)
@require_POST
def mandi_toggle(request, pk):
    m = get_object_or_404(Mandi, pk=pk)
    m.active = not m.active
    m.save(update_fields=["active"])
    messages.success(request, f"Mandi “{m.name}” {'activated' if m.active else 'deactivated'}.")
    return redirect("dashboard:reference_data")


@login_required
@role_required(is_admin)
@require_POST
def commodity_create(request):
    code = (request.POST.get("code") or "").strip().upper()
    name = (request.POST.get("name") or "").strip()
    if not (code and name):
        messages.error(request, "Commodity code and name are required.")
        return redirect("dashboard:reference_data")

    def _int(key, default):
        try:
            return max(0, int(request.POST.get(key) or default))
        except (TypeError, ValueError):
            return default

    if Commodity.objects.filter(code=code).exists():
        messages.error(request, f"Commodity code “{code}” already exists.")
        return redirect("dashboard:reference_data")

    c = Commodity.objects.create(
        code=code, name=name,
        min_particle_area_px=_int("min_particle_area_px", 50),
        max_particle_area_px=_int("max_particle_area_px", 200000),
        expected_min_count=_int("expected_min_count", 20),
        expected_max_count=_int("expected_max_count", 500),
        target_samples=_int("target_samples", 500),
    )
    AuditLog.record(user=request.user, action=AuditAction.USER_CREATE,
                    entity_type="commodity", entity_id=c.id,
                    payload={"code": code, "name": name})
    messages.success(request, f"Commodity “{name}” ({code}) added.")
    return redirect("dashboard:reference_data")


@login_required
@role_required(is_admin)
@require_POST
def commodity_toggle(request, pk):
    c = get_object_or_404(Commodity, pk=pk)
    c.active = not c.active
    c.save(update_fields=["active"])
    messages.success(request, f"Commodity “{c.name}” {'activated' if c.active else 'deactivated'}.")
    return redirect("dashboard:reference_data")
