# GrainVision AI — Data Annotation Platform

A field-deployable Django application for collecting precision grain-quality
training data: an assayer captures a standardized plate image under enforced
quality conditions (step 1), records the physiochemical weights (step 2), then
segments and labels every grain particle — now or later — before a QC reviewer
verifies and approves. Approved data is exported in COCO format for training.

Built to `PTS-GV-ANN-PRD-001 v1.0`.

---

## Highlights

- **Four roles** with a strict authorization matrix (PRD §13.3): Assayer, QC
  Reviewer, Admin, ML Engineer — on a custom `User` model.
- **Physiochemical measurement gate** (PRD §3): five mandatory weights, two
  decimals, defect-sum-≤-total validation, 0.00 confirmation, locked after
  capture.
- **Auto-capture only** (PRD §5): a live client-side CV pipeline (alignment,
  lighting, sharpness) arms a countdown and fires automatically. Manual capture
  is rejected server-side — non-negotiable per the PRD.
- **Segmentation pipeline** (PRD §6): plate isolation → particle segmentation →
  polygon + convexity-defect merge flagging → per-particle features. The engine
  is **SAM2** (required by default; GPU recommended), with a configurable OpenCV
  watershed stand-in for the rare empty-mask case or when SAM2 is intentionally
  disabled.
- **Annotation canvas** (PRD §7): SVG polygon overlays, the six fixed label
  classes with their exact spec colors, 56dp touch targets, draw/delete/undo,
  uncertain marking, live progress.
- **Pre-submit review + cross-validation** (PRD §8): weight-vs-label proportion
  checks surfaced as amber QC notes; submit blocked while any particle is
  unlabeled.
- **QC review** (PRD §9): oldest-first paginated queue with warning badges; a
  split review layout with label toggle, distribution bar, per-particle override
  (always logged, PRD §13.3) and Approve / Rework / Reject.
- **Admin dashboard** (PRD §10): metric cards, label distribution with the <10%
  minority flag, commodity progress vs. target, assayer performance table with
  automated below-threshold alerts; user management.
- **COCO export** (PRD §12, §15.4): pipeline-eligibility gates per submission,
  dataset-readiness gates per commodity, schema-validated before download.
- **Security** (PRD §13): bcrypt (cost 12) hashing, 5-failure/30-minute account
  lockout, append-only audit log, SSL-only DB option, S3 AES-256 option.

---

## Stack

Django 5 · Django REST Framework · **SAM2 + PyTorch** (segmentation engine) ·
OpenCV (headless) + scikit-image + Shapely · WhiteNoise · SQLite (default) or
PostgreSQL · local FS (default) or MinIO/S3.

---

## Project layout

```
grainvision/
├── grainvision/        # settings (env-driven), urls, wsgi/asgi
├── accounts/           # custom User, roles, permissions, lockout, audit middleware
├── core/               # Mandi, Commodity, AuditLog, home router, seed_demo command
├── annotation/         # Submission, Particle, assayer flow, measurement/seg/x-val services
├── ml/                 # sam2_loader, segmentation pipeline, COCO export, health endpoint
├── qc/                 # QC queue, split review, override, approve/rework/reject
├── dashboard/          # admin overview, user mgmt, dataset export, audit viewer
├── templates/          # base + every screen (01–11) following the PRD design
├── static/css/app.css  # navy/teal design system, exact label colors
├── requirements.txt
├── .env.example
├── DEPLOYMENT.md       # install (SAM2/GPU), gunicorn, nginx, PostgreSQL, MinIO
├── smoke.py            # end-to-end flow test (assayer → QC → admin)
└── verify_sam2.py      # confirms the SAM2 path produces masks
```

---

## Quick start (local)

SAM2 is the segmentation engine, so install PyTorch first, then the rest:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install --upgrade pip setuptools wheel

# PyTorch matching your hardware (GPU shown; use .../whl/cpu for CPU):
pip install torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cu121
# App + SAM2 (builds SAM2 against the torch above):
pip install --no-build-isolation -r requirements.txt

# SAM2 checkpoint:
mkdir -p ml_models
curl -L -o ml_models/sam2.1_hiera_small.pt \
  https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt

cp .env.example .env          # set DEBUG=True, SAM2_DEVICE=cuda (or cpu) for local dev
export DEBUG=True

python manage.py migrate
python manage.py collectstatic --noinput
python manage.py seed_demo    # demo data + one user per role
python manage.py runserver
```

Open http://127.0.0.1:8000/ and sign in with a demo account printed by
`seed_demo` (e.g. `ravi / assay12345`, `qc / qc12345678`, `admin / admin12345`).
**Rotate these immediately outside a throwaway environment.**

On a CPU-only box, set `SAM2_DEVICE=cpu` and `SAM2_POINTS_PER_SIDE=16` in `.env`
to keep segmentation latency reasonable. For production (gunicorn, nginx,
PostgreSQL, MinIO, SAM2 on GPU), see **DEPLOYMENT.md**.

---

## End-to-end test

```bash
DEBUG=True DJANGO_SETTINGS_MODULE=grainvision.settings python smoke.py
```

Exercises measurements → auto-capture + segmentation → labeling → submit → QC
override + approve → admin dashboards → schema-validated COCO export, plus the
manual-capture rejection and role-based access guards.

---

## Notes on the segmentation engine

**SAM2 is the segmentation engine.** On first use the model is built once and
cached per worker process. A **CUDA GPU is strongly recommended** (`SAM2_DEVICE=cuda`):
at the default dense point grid it segments a plate in a few seconds. SAM2 also
runs on CPU, but the dense grid is slow there — set `SAM2_POINTS_PER_SIDE=16–24`
for CPU hosts.

By default `SAM2_REQUIRED=True`: if SAM2 cannot load (missing checkpoint, torch
not importable, …) a capture raises a clear `RuntimeError` naming the cause,
rather than silently degrading. Set `SAM2_REQUIRED=False` to allow the classical
OpenCV watershed engine to stand in (it also covers the rare case where SAM2
returns no usable masks for an image, so an assayer is never hard-blocked).

The generator is fully tunable from the environment — `SAM2_POINTS_PER_SIDE`,
`SAM2_POINTS_PER_BATCH`, and the IoU / stability / NMS / min-area thresholds (see
`.env.example`). Polygons, features, the canvas and the COCO export behave
identically regardless of which engine produced the masks.

See **DEPLOYMENT.md** for the full install (PyTorch, the `sam2` package, the
checkpoint download) and a `/api/ml/health/` endpoint that reports the live
engine, device, grid size and any load error.
