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
    rejected_count = submitted.filter(status=SubmissionStatus.QC_REJECTED).count()
    reviewed = submitted.filter(status__in=[
        SubmissionStatus.QC_APPROVED, SubmissionStatus.QC_REJECTED,
    ])
    approval_rate = round(approved.count() / reviewed.count() * 100, 1) if reviewed.count() else 0.0
    pending_qc = submitted.filter(status=SubmissionStatus.PENDING_QC).count()

    # ── Filters for the label distribution (mandi / assayer / date) ──
    f_mandi = request.GET.get("mandi") or ""
    f_assayer = request.GET.get("assayer") or ""
    f_from = request.GET.get("from") or ""
    f_to = request.GET.get("to") or ""
    parts = Particle.objects.filter(
        submission__status=SubmissionStatus.QC_APPROVED
    ).exclude(label=ParticleLabel.UNLABELED)
    if f_mandi:
        parts = parts.filter(submission__mandi_id=f_mandi)
    if f_assayer:
        parts = parts.filter(submission__assayer_id=f_assayer)
    if f_from:
        parts = parts.filter(submission__submitted_at__date__gte=f_from)
    if f_to:
        parts = parts.filter(submission__submitted_at__date__lte=f_to)

    dist = {l.value: 0 for l in ParticleLabel if l != ParticleLabel.UNLABELED}
    for r in parts.values("label").annotate(n=Count("id")):
        if r["label"] in dist:
            dist[r["label"]] = r["n"]
    grand = sum(dist.values()) or 1
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

    # ── Samples completed per assayer, broken down by mandi (item 1) ──
    am = (submitted.values("assayer__first_name", "assayer__last_name",
                           "assayer__username", "mandi__name")
          .annotate(n=Count("id")).order_by("assayer__first_name", "mandi__name"))
    grouped = {}
    for r in am:
        who = (f"{r['assayer__first_name']} {r['assayer__last_name']}".strip()
               or r["assayer__username"] or "—")
        grouped.setdefault(who, []).append({"mandi": r["mandi__name"] or "—", "n": r["n"]})
    assayer_mandi = [
        {"name": who, "rows": rows, "total": sum(x["n"] for x in rows)}
        for who, rows in grouped.items()
    ]

    return render(request, "dashboard/overview.html", {
        "total_submissions": total_submissions,
        "approval_rate": approval_rate,
        "pending_qc": pending_qc,
        "rejected_count": rejected_count,
        "label_dist": label_dist,
        "progress": progress,
        "assayer_mandi": assayer_mandi,
        "minority_threshold": MINORITY_THRESHOLD,
        "mandis": Mandi.objects.all(),
        "assayers": User.objects.filter(role=Role.ASSAYER),
        "f_mandi": f_mandi, "f_assayer": f_assayer, "f_from": f_from, "f_to": f_to,
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
def user_edit(request, pk):
    u = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        first, _, last = name.partition(" ")
        u.first_name, u.last_name = first, last
        u.email = request.POST.get("email", "").strip()
        role = request.POST.get("role")
        if role in Role.values:
            u.role = role
        u.phone = request.POST.get("phone", "").strip()
        u.is_active = request.POST.get("is_active") == "on"
        u.save()
        u.mandis.set(request.POST.getlist("mandis"))
        pw = request.POST.get("password", "")
        if pw:
            u.set_password(pw)
            u.save()
        AuditLog.record(user=request.user, action=AuditAction.USER_UPDATE,
                        entity_type="user", entity_id=u.id, payload={"role": u.role})
        messages.success(request, f"User {u} updated.")
        return redirect("dashboard:user_management")
    return render(request, "dashboard/user_edit.html", {
        "u": u, "roles": Role.choices,
        "mandis": Mandi.objects.all().order_by("name"),
        "user_mandis": set(u.mandis.values_list("id", flat=True)),
    })


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
    try:
        data, n_images, n_ann = coco_export.build_export_zip(commodity)
    except Exception as e:
        return HttpResponse(f"Export failed: {e}", content_type="text/plain", status=500)

    if n_images == 0:
        return HttpResponse(
            "Nothing to export yet — there are no QC-approved samples with labeled "
            "grains" + (f" for {code}." if code else "."),
            content_type="text/plain", status=200)

    AuditLog.record(user=request.user, action=AuditAction.EXPORT,
                    entity_type="dataset", entity_id=code or "all",
                    payload={"images": n_images, "annotations": n_ann})

    fname = f"grainvision_{code or 'all'}_coco.zip"
    resp = HttpResponse(data, content_type="application/zip")
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
    all_active_commodities = list(Commodity.objects.filter(active=True))
    mandis = Mandi.objects.all().order_by("-active", "name").prefetch_related("commodities")
    mandi_rows = []
    for m in mandis:
        chosen = set(m.commodities.values_list("id", flat=True))
        mandi_rows.append({
            "obj": m,
            "chosen_ids": chosen,
            "chosen_names": ", ".join(c.name for c in m.commodities.all()) or "— none —",
        })
    commodities = Commodity.objects.all().order_by("-active", "name")
    rows = []
    for c in commodities:
        approved = Submission.objects.filter(commodity=c, status=SubmissionStatus.QC_APPROVED).count()
        rows.append({"obj": c, "approved": approved})
    return render(request, "dashboard/reference_data.html", {
        "mandi_rows": mandi_rows,
        "all_commodities": all_active_commodities,
        "commodities": rows,
    })


@login_required
@role_required(is_admin)
@require_POST
def mandi_set_commodities(request, pk):
    m = get_object_or_404(Mandi, pk=pk)
    ids = request.POST.getlist("commodities")
    m.commodities.set(Commodity.objects.filter(id__in=ids, active=True))
    AuditLog.record(user=request.user, action=AuditAction.USER_UPDATE,
                    entity_type="mandi", entity_id=m.id,
                    payload={"commodities": list(map(int, ids))})
    messages.success(request, f"Updated commodities for “{m.name}”.")
    return redirect("dashboard:reference_data")


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


# ── Admin: browse everything submitted (read-only) with full filters ──
@login_required
@role_required(is_admin)
def submissions(request):
    from annotation.models import SubmissionStatus as SS
    qs = (Submission.objects.filter(submitted_at__isnull=False)
          .select_related("commodity", "mandi", "assayer", "qc_reviewer")
          .order_by("-submitted_at"))
    f = {k: (request.GET.get(k) or "") for k in
         ("mandi", "commodity", "assayer", "qc", "status", "from", "to")}
    if f["mandi"]:      qs = qs.filter(mandi_id=f["mandi"])
    if f["commodity"]:  qs = qs.filter(commodity__code=f["commodity"])
    if f["assayer"]:    qs = qs.filter(assayer_id=f["assayer"])
    if f["qc"]:         qs = qs.filter(qc_reviewer_id=f["qc"])
    if f["status"]:     qs = qs.filter(status=f["status"])
    if f["from"]:       qs = qs.filter(submitted_at__date__gte=f["from"])
    if f["to"]:         qs = qs.filter(submitted_at__date__lte=f["to"])

    from django.core.paginator import Paginator
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "dashboard/submissions.html", {
        "page": page, "f": f,
        "mandis": Mandi.objects.all().order_by("name"),
        "commodities": Commodity.objects.all().order_by("name"),
        "assayers": User.objects.filter(role=Role.ASSAYER),
        "reviewers": User.objects.filter(role=Role.QC_REVIEWER),
        "statuses": [(v, l) for v, l in SS.choices if v != SS.DRAFT],
        "total": qs.count(),
    })


@login_required
@role_required(is_admin)
@require_POST
def admin_rework(request, pk):
    sub = get_object_or_404(Submission, pk=pk, submitted_at__isnull=False)
    sub.status = SubmissionStatus.REWORK_REQUESTED
    sub.rework_instructions = request.POST.get("notes", "")[:2000] or "Returned by admin for rework."
    sub.submitted_at = None
    sub.save(update_fields=["status", "rework_instructions", "submitted_at"])
    AuditLog.record(user=request.user, action=AuditAction.QC_REWORK,
                    entity_type="submission", entity_id=sub.id,
                    payload={"by": "admin"})
    messages.success(request, f"{sub.short_id} sent back to the assayer for rework.")
    return redirect("dashboard:submissions")


# ── Admin: read-only view of one submission (annotated plate + labels) ──
@login_required
@role_required(is_admin)
def submission_detail(request, pk):
    import json
    from annotation.models import ParticleLabel, LABEL_COLORS
    sub = get_object_or_404(
        Submission.objects.select_related("commodity", "mandi", "assayer", "qc_reviewer"),
        pk=pk)
    particles = list(sub.particles.all())
    counts = {}
    for p in particles:
        counts[p.effective_label] = counts.get(p.effective_label, 0) + 1
    label_rows = [
        {"label": ParticleLabel(k).label, "color": LABEL_COLORS[ParticleLabel(k)], "n": v}
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    particles_json = json.dumps([
        {"polygon": p.polygon,
         "color": LABEL_COLORS[ParticleLabel(p.effective_label)],
         "unlabeled": p.effective_label == ParticleLabel.UNLABELED}
        for p in particles
    ])
    return render(request, "dashboard/submission_detail.html", {
        "sub": sub, "label_rows": label_rows,
        "particles_json": particles_json,
        "crop_size": (sub.capture_quality_scores or {}).get("crop_size", [1000, 1000]),
        "total": len(particles),
        "unlabeled": sum(1 for p in particles if p.effective_label == ParticleLabel.UNLABELED),
    })
