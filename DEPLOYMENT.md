# GrainVision AI — Data Annotation Platform
## Deployment Guide

The platform uses **SAM2 (Segment Anything Model 2)** as its segmentation
engine. A **CUDA GPU is strongly recommended** — with a GPU, automatic mask
generation runs in a few seconds per plate at the default dense point grid. SAM2
also runs on CPU, but the dense grid is slow there; lower `SAM2_POINTS_PER_SIDE`
to 16–24 for CPU hosts.

| Component | Default | Notes |
|-----------|---------|-------|
| Web/app   | gunicorn + WhiteNoise | static served by the app, no CDN needed |
| Database  | SQLite | set `USE_POSTGRES=True` for PostgreSQL |
| Image storage | local filesystem | set `USE_S3=True` for MinIO/S3 |
| Segmentation | **SAM2 (required)** | GPU recommended; `SAM2_REQUIRED=True` by default |

---

## 1. Prerequisites

- Python 3.11 or 3.12
- A CUDA-capable GPU + recent NVIDIA driver (recommended) — or CPU
- OS packages for OpenCV (headless): `libgl1`, `libglib2.0-0`

```bash
sudo apt-get install -y python3 python3-venv python3-dev libgl1 libglib2.0-0
```

---

## 2. Install

```bash
cd grainvision
python3 -m venv .venv && . .venv/bin/activate
pip install --upgrade pip setuptools wheel

# 2.1 Install PyTorch matching your hardware FIRST (so the correct build is used):
#   GPU (CUDA 12.1):
pip install torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cu121
#   CPU only:
# pip install torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cpu

# 2.2 Install the application + SAM2 (and remaining deps).
#     --no-build-isolation makes SAM2 build against the torch you just installed
#     instead of pulling its own default (CUDA) torch.
pip install --no-build-isolation -r requirements.txt
```

If SAM2's build complains about a CUDA extension on a CPU-only box, prefix the
install with `SAM2_BUILD_CUDA=0`:

```bash
SAM2_BUILD_CUDA=0 pip install --no-build-isolation -r requirements.txt
```

Verify the stack imports:

```bash
python -c "import torch, sam2, cv2, django, bcrypt; print('torch', torch.__version__, '| cuda', torch.cuda.is_available())"
```

---

## 3. Download the SAM2 checkpoint

The platform defaults to the **hiera-small** checkpoint:

```bash
mkdir -p ml_models
curl -L -o ml_models/sam2.1_hiera_small.pt \
  https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt
```

The matching config (`configs/sam2.1/sam2.1_hiera_s.yaml`) ships inside the
installed `sam2` package, so no extra config file is needed. To use a different
size, download that checkpoint and set `SAM2_CHECKPOINT` + `SAM2_MODEL_CFG`
accordingly (e.g. `sam2.1_hiera_large.pt` with `.../sam2.1_hiera_l.yaml`).

---

## 4. Configure

```bash
cp .env.example .env
```

Edit `.env`:

- `SECRET_KEY` — `python -c "import secrets; print(secrets.token_urlsafe(50))"`
- `DEBUG=False`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` for production.
- `SAM2_DEVICE=cuda` on a GPU host; `SAM2_DEVICE=cpu` otherwise.
- On CPU, set `SAM2_POINTS_PER_SIDE=16` (or `24`) to keep latency reasonable.
- Leave `SAM2_REQUIRED=True` so a misconfiguration fails loudly rather than
  silently degrading to the classical engine. (Set `False` only if you
  intentionally want the OpenCV watershed engine to stand in.)

---

## 5. Database, static, seed

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser        # or: python manage.py seed_demo
```

`seed_demo` loads four mandis, four commodities (Wheat, Rice, Chickpea, Lentil —
each targeting 500 approved samples) and one account per role. **Rotate the
printed demo passwords immediately** outside a throwaway environment.

---

## 6. Run

### Development

```bash
python manage.py runserver 0.0.0.0:8000
```

### Production (gunicorn + WhiteNoise)

```bash
gunicorn grainvision.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 180
```

The model is loaded lazily on the first segmentation and cached per worker
process. With multiple gunicorn workers, each holds its own copy — size
`--workers` to your GPU memory. `--timeout 180` gives headroom for the first
(model-loading) request.

### systemd

`/etc/systemd/system/grainvision.service`:

```ini
[Unit]
Description=GrainVision AI Annotation Platform
After=network.target

[Service]
User=grainvision
WorkingDirectory=/opt/grainvision
EnvironmentFile=/opt/grainvision/.env
ExecStart=/opt/grainvision/.venv/bin/gunicorn grainvision.wsgi:application \
    --bind 127.0.0.1:8000 --workers 2 --timeout 180
Restart=always

[Install]
WantedBy=multi-user.target
```

### nginx (TLS 1.3, PRD §13.2)

```nginx
server {
    listen 443 ssl;
    server_name grainvision.example.com;
    ssl_certificate     /etc/ssl/grainvision/fullchain.pem;
    ssl_certificate_key /etc/ssl/grainvision/privkey.pem;
    ssl_protocols TLSv1.3 TLSv1.2;
    client_max_body_size 25M;            # 12MP JPEG uploads

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Static and media are served by WhiteNoise from the app; no extra nginx blocks
are required.

---

## 7. PostgreSQL (optional)

```bash
# .env
USE_POSTGRES=True
POSTGRES_DB=grainvision
POSTGRES_USER=grainvision
POSTGRES_PASSWORD=<strong-secret>
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_SSLMODE=require        # PRD §13.2: SSL-only
```

Then re-run `migrate`, `collectstatic`, `createsuperuser`.

---

## 8. MinIO / S3 image storage (optional)

PRD §13.2: AES-256 at rest, 15-minute pre-signed URLs.

```bash
# .env
USE_S3=True
S3_ENDPOINT_URL=http://minio.internal:9000
S3_ACCESS_KEY=grainvision
S3_SECRET_KEY=<strong-secret>
S3_BUCKET=grainvision
```

Raw originals are written once and never overwritten (PRD §5.3 / §11.3).

---

## 9. Verify the segmentation engine

After deployment, confirm SAM2 is the live engine:

```bash
python -c "import django,os; os.environ['DJANGO_SETTINGS_MODULE']='grainvision.settings'; \
django.setup(); from ml import sam2_loader; \
print('available:', sam2_loader.available()); \
print('generator:', type(sam2_loader.get_mask_generator()).__name__)"
```

Or hit the authenticated health endpoint, which reports
`engine`, `device`, `points_per_side`, `sam2_available` and any `load_error`:

```
GET /api/ml/health/
```

If `SAM2_REQUIRED=True` and SAM2 cannot load, a capture attempt raises a clear
`RuntimeError` naming the exact cause (missing checkpoint, torch not importable,
etc.) instead of silently producing lower-quality results.

---

## 10. Post-deploy checklist

```bash
python manage.py check --deploy
python manage.py migrate --check
```

In the browser:

- [ ] Each role logs in and lands on the right home.
- [ ] Measurements screen blocks "Proceed" until five valid weights with defect
      sum <= total; a 0.00 field triggers the confirm modal.
- [ ] Capture is auto-only (no manual shutter); after capture the canvas shows
      **SAM2** polygons (check `/api/ml/health/` shows `engine: sam2`).
- [ ] Labeling all particles enables "Review & submit"; submitting with any
      unlabeled particle is blocked.
- [ ] QC queue is oldest-first with warning badges; review approves/reworks/
      rejects; per-particle override is logged (`/admin/audit/`).
- [ ] Admin dashboard shows metric cards, the <10% minority flag, commodity
      progress and the assayer table.
- [ ] Dataset export downloads a schema-valid COCO JSON.
- [ ] 5 failed logins trigger a 30-minute lockout.

A scripted end-to-end check is included:

```bash
DEBUG=True DJANGO_SETTINGS_MODULE=grainvision.settings python smoke.py
```

(`smoke.py` exercises the whole assayer -> QC -> admin flow. To run it quickly on
a CPU box without waiting on SAM2, set `SAM2_ENABLED=False SAM2_REQUIRED=False`
so the flow uses the fast classical engine; the HTTP plumbing it verifies is
identical either way. `verify_sam2.py` separately confirms the SAM2 path itself.)

---

## 11. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `pip` pulls a huge CUDA torch while building SAM2 | Install torch first, then `pip install --no-build-isolation -r requirements.txt`. |
| SAM2 build errors about a CUDA extension on CPU | Prefix with `SAM2_BUILD_CUDA=0`. |
| `RuntimeError: SAM2 ... not available: Checkpoint not found` | Download the checkpoint to `SAM2_CHECKPOINT` (step 3). |
| `RuntimeError: SAM2 ... 'sam2' is not importable` | The `sam2` package didn't install; re-run step 2.2. |
| Segmentation very slow | You're on CPU. Use a GPU (`SAM2_DEVICE=cuda`) or lower `SAM2_POINTS_PER_SIDE`. |
| `ImportError: libGL.so.1` | Install `libgl1` and `libglib2.0-0`. |
| `CSRF verification failed` | Add the browse origin to `CSRF_TRUSTED_ORIGINS`. |

---

## 12. Backups & retention (PRD §11.3)

- **DB:** SQLite — copy `db.sqlite3`; PostgreSQL — scheduled `pg_dump`.
- **Images:** local FS — back up `media/`; MinIO — bucket replication. Originals
  are immutable and retained indefinitely; rejected-submission images are kept
  but excluded from exports.
- **Audit log:** append-only; retain >= 1 year.
