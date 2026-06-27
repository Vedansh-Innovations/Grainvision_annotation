# GrainVision — per-grain segmentation on Azure (cheap, serverless GPU)

This is the setup for **true per-grain** segmentation that stays cheap. The trick:
keep the app on a small CPU box, and put **only the GPU segmentation** on an Azure
Container Apps **serverless GPU** that scales to zero and is billed per second — no usage charges when idle. You pay GPU
money only for the few seconds each capture is actually segmenting.

```
 Browser
   │ HTTPS
   ▼
 ┌─────────────────────────────┐     per capture (HTTPS + token)     ┌──────────────────────────────┐
 │  Web app (Django)           │ ─────────────────────────────────▶ │  GPU worker (FastAPI + SAM2)  │
 │  App Service B1 (CPU, cheap)│ ◀───────── per-grain polygons ───── │  Container Apps, T4 GPU       │
 │  always on, lean image      │                                     │  SCALE-TO-ZERO when idle      │
 └───────────┬─────────────────┘                                     └──────────────────────────────┘
             │
   Postgres (B1ms)   Azure Files (media)
```

Why this is the per-grain answer: the worker runs SAM2 **tiled** (`SAM2_TILES=True`)
with a dense grid + crop layers, so dense clusters get split into individual
grains — but only on the GPU, where that's fast (seconds), and only when a capture
happens.

---

## Cost

| Piece | What | Rough monthly |
|---|---|---|
| Web app | App Service **B1** (CPU, always on, lean image) | ~$13 |
| Database | PostgreSQL **B1ms** (stop when idle to save more) | ~$13 |
| GPU worker | Container Apps **Consumption-GPU-NC8as-T4**, scale-to-zero, per-second | **only while segmenting** — often ~$5–20 |
| Registry/storage | ACR Basic + Storage LRS | ~$5 |

**~$35–50/month** for genuinely fast per-grain, and the GPU portion tracks your
actual usage because when the app scales to zero, GPU billing stops. Exact GPU price is
region-specific and per-second, so confirm in the Azure Pricing Calculator / Cost
Management once live.

> **The one tradeoff: cold starts.** With scale-to-zero, the *first* capture after
> an idle period spins up the GPU and loads the model (~1–3 min). During an active
> labeling session the worker stays warm, so only the first capture waits. If you
> want zero cold starts, set the worker's **min replicas = 1** (you then pay for
> the GPU while it's on — turn it back to 0 after the session). You can also cut
> cold starts with Azure Container Registry artifact streaming (Premium ACR).

---

## Before you start (manual)

1. **GPU region.** Serverless GPUs are in a limited set of regions —
   West US 3, Australia East, and Sweden Central (more since). Pick **Sweden Central**
   (closest GA option to India; an image round-trip is small, so latency is fine).
2. **GPU quota.** You need serverless GPU quota; you submit the request via a customer support case.
   Portal → **Help + support → Create a support request** → Quota type
   **“Managed Environment Consumption T4 GPUs”**. Approval is usually quick. (Many
   pay-as-you-go/EA subscriptions already have one T4 quota.)
3. **GitHub repo** with this project (Dockerfile, `Dockerfile.web`, and the
   `worker/` folder all included), as in `DEPLOY_AZURE_PORTAL.md` step 1, plus a
   GitHub token.
4. Pick a **shared secret** string now (any 24+ random chars) — call it
   `WORKER_TOKEN`. The web app sends it; the worker checks it.

---

## 1. Build the two images (Container Registry)

You build **two** images from the same repo: the lean web app and the GPU worker.

1. Create a **Container Registry** (Basic, Admin user enabled) — same as the portal
   guide step 2.
2. Registry → **Services → Tasks → + Add**, create the **web** image:
   - Image: `grainvision-web:latest`
   - **Dockerfile:** `Dockerfile.web`   ← the lean, no-torch image
   - Source: your GitHub repo / branch `main` / GitHub token
   - Create → open the task → **Run**.
3. **Add a second task** for the **worker**:
   - Image: `grainvision-worker:latest`
   - **Dockerfile:** `worker/Dockerfile`
   - **Build context path:** `worker` (so the build runs inside the worker folder)
   - Source: same repo. Create → **Run**.
   - The worker build is large (CUDA + torch + SAM2) — give it ~15–20 min.

When both show **Succeeded**, you'll have `grainvision-web` and
`grainvision-worker` under **Repositories**.

---

## 2. Database + media storage

Create these exactly as in `DEPLOY_AZURE_PORTAL.md`:
- **PostgreSQL Flexible Server** (Burstable B1ms), DB `grainvision`, allow Azure
  services. (Put it in the **same region**, Sweden Central.)
- **Storage account** + **File share** named `media`; copy an access key.

---

## 3. Container Apps environment + GPU profile

1. Portal → **Create a resource → Container Apps Environment** (or it's created
   with the first Container App). Region **Sweden Central**. Enable **workload
   profiles**.
2. In the environment → **Workload profiles → + Add** →
   profile type **Consumption-GPU-NC8as-T4** (1× NVIDIA T4). Save.

---

## 4. GPU worker (Container App, scale-to-zero)

1. Portal → **Create a resource → Container App**.
2. Basics: same resource group, region Sweden Central, the environment from step 3.
3. **Container** tab:
   - Image source: **Azure Container Registry** → your registry →
     image `grainvision-worker:latest`.
   - **Workload profile:** select the **NC8as-T4 (GPU)** profile.
   - Under resource allocation, the **GPU** option should be selected for that
     profile.
4. **Ingress** tab: **Enabled**, **Accepting traffic from anywhere**,
   **Target port = 8080**.
5. Create. Then open the app → **Application → Containers → Environment variables**
   (or Revision management) and set:

   ```
   SAM2_DEVICE            = cuda
   SAM2_TILES             = True
   SAM2_POINTS_PER_SIDE   = 64
   SAM2_CROP_N_LAYERS     = 1
   SAM2_TILE_SIZE         = 0
   WORKER_TOKEN           = <your shared secret>
   ```
6. **Scale** tab: **Min replicas = 0**, **Max replicas = 1** (scale-to-zero). Save
   (creates a new revision).
7. Copy the worker's **Application URL** (e.g.
   `https://grainvision-worker.<hash>.swedencentral.azurecontainerapps.io`).
   Test it: open `…/health` in a browser — it should report `cuda_available: true`
   once the first request warms it up.

---

## 5. Web app (App Service B1, lean image)

1. Create a **Web App** (Publish = **Container**, Linux, **Basic B1**), image
   `grainvision-web:latest` from your registry — same flow as the portal guide
   step 5.
2. **Environment variables → Advanced edit**, paste and fill in:

   ```json
   [
     { "name": "WEBSITES_PORT", "value": "8000" },
     { "name": "WEBSITES_ENABLE_APP_SERVICE_STORAGE", "value": "false" },
     { "name": "WEBSITES_CONTAINER_START_TIME_LIMIT", "value": "600" },
     { "name": "DEBUG", "value": "False" },
     { "name": "SECRET_KEY", "value": "<50+ random chars>" },
     { "name": "ALLOWED_HOSTS", "value": "<your-app>.azurewebsites.net" },
     { "name": "CSRF_TRUSTED_ORIGINS", "value": "https://<your-app>.azurewebsites.net" },
     { "name": "SECURE_SSL_REDIRECT", "value": "True" },

     { "name": "USE_POSTGRES", "value": "True" },
     { "name": "POSTGRES_DB", "value": "grainvision" },
     { "name": "POSTGRES_USER", "value": "gvadmin" },
     { "name": "POSTGRES_PASSWORD", "value": "<pg password>" },
     { "name": "POSTGRES_HOST", "value": "<pg-server>.postgres.database.azure.com" },
     { "name": "POSTGRES_PORT", "value": "5432" },
     { "name": "POSTGRES_SSLMODE", "value": "require" },

     { "name": "USE_S3", "value": "False" },
     { "name": "SERVE_MEDIA", "value": "True" },

     { "name": "SAM2_ENABLED", "value": "False" },
     { "name": "SAM2_REMOTE_URL", "value": "https://<worker-fqdn>" },
     { "name": "SAM2_REMOTE_TOKEN", "value": "<same shared secret>" },
     { "name": "SAM2_REMOTE_TIMEOUT", "value": "180" },

     { "name": "GUNICORN_WORKERS", "value": "2" },
     { "name": "GUNICORN_TIMEOUT", "value": "300" },

     { "name": "DJANGO_SUPERUSER_USERNAME", "value": "admin" },
     { "name": "DJANGO_SUPERUSER_PASSWORD", "value": "<admin password>" },
     { "name": "DJANGO_SUPERUSER_EMAIL", "value": "admin@example.com" },
     { "name": "SEED_DEMO", "value": "false" }
   ]
   ```

   The key lines: `SAM2_ENABLED=False` (the web app never loads SAM2 itself) and
   `SAM2_REMOTE_URL` + `SAM2_REMOTE_TOKEN` (it offloads to the worker).
3. **Configuration → Path mappings** → mount the `media` file share at
   `/app/media` (portal guide step 7).
4. **Restart**, watch **Log stream** for `starting gunicorn`, open the URL, sign in
   as `admin`.

---

## 6. Test per-grain

1. Sign in, start a sample, capture a plate. The first capture after idle waits
   for the GPU worker to warm up (cold start); after that it's seconds.
2. On the annotation canvas you should now see **individual grain polygons**,
   including inside the dense clusters — that's the tiled SAM2 on the T4.
3. The worker scales back to zero a few minutes after the last capture, so you stop
   paying for the GPU automatically.

---

## Tuning per-grain accuracy vs. cost (worker env)

- **More recall on tiny/dense grains:** raise `SAM2_POINTS_PER_SIDE` (64 → 96/128)
  and keep `SAM2_CROP_N_LAYERS=1`. Slower per capture, but on a T4 still seconds.
- **Best mask quality:** rebuild the worker image with the **hiera-large**
  checkpoint (Dockerfile build args `SAM2_CKPT_URL`/`SAM2_CKPT_NAME` +
  `SAM2_MODEL_CFG=configs/sam2.1/sam2.1_hiera_l.yaml`). Bigger, a bit slower.
- **Smaller/larger grains:** the min/max grain area comes from each commodity's
  settings in the app (Mandis & commodities), so set realistic pixel areas there.
- **Fewer fragments / merges:** adjust `SAM2_TILE_SIZE` (0 = auto ≈ 3–4 tiles per
  side); larger tiles = fewer seams but coarser, smaller tiles = more zoom on tiny
  grains.

## Notes

- **Security:** the worker is public but requires the `X-Worker-Token` header
  (`WORKER_TOKEN`), so only your web app can call it. Keep the secret out of the
  repo (it lives only in app settings).
- **No GPU yet / quota pending?** Set the web app to `SAM2_ENABLED=True`,
  `SAM2_DEVICE=cpu`, `SAM2_REMOTE_URL=""` to run on CPU in the meantime (per-grain
  will be slow; see `DEPLOY_AZURE_PORTAL.md` cheap mode).
- **Updating:** push to GitHub → the ACR tasks rebuild → restart the web app and/or
  create a new worker revision.
