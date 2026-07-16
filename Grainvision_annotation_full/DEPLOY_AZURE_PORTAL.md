# Deploying GrainVision AI to Azure — Portal (no CLI)

Everything here is done in the **Azure Portal** (portal.azure.com) plus a free
**GitHub** account. No command line.

Why GitHub? The app ships as a Docker image, and the only no-CLI way to *build*
that image is to let Azure Container Registry build it straight from a GitHub
repo. After that, every step is clicking in the portal.

**Order of work**
1. Put the code on GitHub
2. Create a Container Registry and build the image from GitHub
3. Create the PostgreSQL database
4. Create the Storage account + file share (for uploaded images)
5. Create the Web App (container) from your image
6. Add the settings (environment variables)
7. Mount the file share for media
8. Restart, watch logs, sign in

Budget ~30–40 minutes, most of which is the image build running on its own.

> **Cheapest multi-user setup (with SAM2 on CPU).** This guide is tuned for the
> lowest cost that still works for a small team:
> - App Service **Basic B2** (~$26/mo) — the smallest plan that fits SAM2.
> - PostgreSQL **Burstable B1ms** (~$13/mo) — and you can **Stop** the server
>   when nobody's working to pay even less.
> - **One gunicorn worker with 4 threads** (set in the env vars): SAM2 loads into
>   memory only **once** and is shared across users, instead of each worker
>   loading its own multi-GB copy. Multiple assayers can label/navigate at the
>   same time; heavy captures run one-at-a-time (a few seconds each on CPU).
> - `SAM2_POINTS_PER_SIDE=12` keeps each capture fast on CPU.
>
> Expect **~$40/month total**. To go cheaper still, set `SAM2_ENABLED=False`
> and drop to a **B1** plan (~$25/mo total) — same workflow, lighter classic-CV
> segmentation instead of SAM2.

---

## 1. Put the code on GitHub

1. Go to **github.com** → **New repository** → name it `grainvision` →
   **Private** is fine → **Create**.
2. Upload the project: on the new repo page click **uploading an existing file**,
   then drag in the **contents** of the project folder (the folder that contains
   `Dockerfile`, `manage.py`, `requirements.txt`, etc.). Commit.
   - Make sure `Dockerfile` sits at the **root** of the repo (not inside a
     subfolder).
3. Create a **Personal Access Token** so Azure can read the repo:
   GitHub → your avatar → **Settings** → **Developer settings** →
   **Personal access tokens** → **Tokens (classic)** → **Generate new token** →
   tick **repo** scope → generate → **copy the token** (you'll paste it in step 2).

---

## 2. Container Registry + build the image

**Create the registry**
1. Portal → **Create a resource** → search **Container Registry** → **Create**.
2. Resource group: **Create new** → `grainvision-rg`.
   Registry name: e.g. `grainvisionacr123` (must be globally unique, letters/numbers).
   Location: pick one near you (e.g. **Central India**). SKU: **Basic**.
3. **Review + create** → **Create**. Wait for deployment, then **Go to resource**.
4. Left menu → **Settings → Access keys** → toggle **Admin user = Enabled**
   (the Web App uses this to pull the image later).

**Build the image from GitHub (ACR Task)**
5. In the registry, left menu → **Services → Tasks** → **+ Add**.
6. Fill in:
   - **Task name:** `build-grainvision`
   - **Image:** `grainvision:latest`
   - **Dockerfile:** `Dockerfile`
   - **Source location → Repository:** `https://github.com/<you>/grainvision.git`
   - **Branch:** `main`
   - **Source control authentication:** paste the **GitHub token** from step 1.
   - (You can leave the commit trigger on — it rebuilds when you push changes.)
7. **Create.** Then open the task and click **Run** (top bar). Confirm.
8. Watch the build log. **This takes ~10–20 minutes** (it compiles PyTorch + SAM2
   and downloads the model). When it shows **Succeeded**, the image is ready —
   you'll see it under **Services → Repositories → grainvision**.

> If the Run button asks for a "source trigger token" again, it's the same
> GitHub PAT.

---

## 3. PostgreSQL database

1. Portal → **Create a resource** → search **Azure Database for PostgreSQL
   Flexible Server** → **Create**.
2. Basics:
   - Resource group: `grainvision-rg`
   - Server name: e.g. `grainvision-pg-123` (globally unique)
   - Region: same as the registry
   - PostgreSQL version: **16**
   - Workload type: **Development** (this picks the cheap **Burstable B1ms**)
   - Authentication: **PostgreSQL authentication only**
   - Admin username: `gvadmin` · set a strong **password** (save it)
3. **Networking** tab:
   - Connectivity method: **Public access**
   - Tick **Allow public access from any Azure service within Azure to this
     server**.
   - (Optional) **+ Add current client IP** if you want to connect from your PC.
4. **Review + create** → **Create**. Wait, then **Go to resource**.
5. Left menu → **Settings → Databases** → **+ Add** → name it **`grainvision`** →
   Save.
6. From the server **Overview**, copy the **Server name**
   (looks like `grainvision-pg-123.postgres.database.azure.com`) — you'll need it
   in step 6.

---

## 4. Storage account + file share (for uploaded plate images)

1. Portal → **Create a resource** → **Storage account** → **Create**.
2. Basics:
   - Resource group: `grainvision-rg`
   - Name: e.g. `grainvisionst123` (3–24 lowercase letters/numbers, unique)
   - Region: same as the rest
   - Performance: **Standard** · Redundancy: **LRS**
3. **Review + create** → **Create** → **Go to resource**.
4. Left menu → **Data storage → File shares** → **+ File share** → name **`media`**
   → Create.
5. Left menu → **Security + networking → Access keys** → **Show** key1 → copy the
   **Key** value (you'll paste it in step 7).

---

## 5. Web App (container)

1. Portal → **Create a resource** → **Web App** → **Create**.
2. **Basics:**
   - Resource group: `grainvision-rg`
   - Name: e.g. `grainvision-app-123` → your URL becomes
     `https://grainvision-app-123.azurewebsites.net` (globally unique)
   - Publish: **Container**
   - Operating System: **Linux**
   - Region: same as the rest
   - Pricing plan: **Create new** App Service Plan → pick **Basic B2**
     (3.5 GB RAM — the cheapest tier that fits SAM2 + PyTorch in memory, ~$26/mo).
     B1 is too small for SAM2; only use B1 if you set `SAM2_ENABLED=False`.
3. **Container** tab:
   - Image Source: **Azure Container Registry**
   - Registry: your `grainvisionacr123`
   - Image: **grainvision**
   - Tag: **latest**
   - Leave **Startup Command** blank (the image starts itself).
4. **Review + create** → **Create** → **Go to resource**.

---

## 6. Add the settings (environment variables)

1. In the Web App, left menu → **Settings → Environment variables**
   (older portals: **Configuration → Application settings**).
2. Click **Advanced edit**, then **paste the JSON below**, replacing every
   `<...>` placeholder. Save.

```json
[
  { "name": "WEBSITES_PORT", "value": "8000" },
  { "name": "WEBSITES_ENABLE_APP_SERVICE_STORAGE", "value": "false" },
  { "name": "WEBSITES_CONTAINER_START_TIME_LIMIT", "value": "1800" },
  { "name": "DEBUG", "value": "False" },
  { "name": "SECRET_KEY", "value": "<paste-a-50-char-random-string>" },
  { "name": "ALLOWED_HOSTS", "value": "<your-app>.azurewebsites.net" },
  { "name": "CSRF_TRUSTED_ORIGINS", "value": "https://<your-app>.azurewebsites.net" },
  { "name": "SECURE_SSL_REDIRECT", "value": "True" },
  { "name": "USE_POSTGRES", "value": "True" },
  { "name": "POSTGRES_DB", "value": "grainvision" },
  { "name": "POSTGRES_USER", "value": "gvadmin" },
  { "name": "POSTGRES_PASSWORD", "value": "<your-postgres-password>" },
  { "name": "POSTGRES_HOST", "value": "<your-pg-server>.postgres.database.azure.com" },
  { "name": "POSTGRES_PORT", "value": "5432" },
  { "name": "POSTGRES_SSLMODE", "value": "require" },
  { "name": "USE_S3", "value": "False" },
  { "name": "SERVE_MEDIA", "value": "True" },
  { "name": "SAM2_ENABLED", "value": "True" },
  { "name": "SAM2_REQUIRED", "value": "True" },
  { "name": "SAM2_DEVICE", "value": "cpu" },
  { "name": "SAM2_POINTS_PER_SIDE", "value": "12" },
  { "name": "GUNICORN_WORKERS", "value": "1" },
  { "name": "GUNICORN_THREADS", "value": "4" },
  { "name": "GUNICORN_TIMEOUT", "value": "300" },
  { "name": "DJANGO_SUPERUSER_USERNAME", "value": "admin" },
  { "name": "DJANGO_SUPERUSER_PASSWORD", "value": "<choose-a-strong-admin-password>" },
  { "name": "DJANGO_SUPERUSER_EMAIL", "value": "admin@example.com" },
  { "name": "SEED_DEMO", "value": "false" }
]
```

Tips for the placeholders:
- **SECRET_KEY** — any long random string (50+ characters). A password manager's
  "generate password" works.
- **ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS** — your exact app host from step 5.
- **POSTGRES_HOST / POSTGRES_PASSWORD** — from step 3.
- **DJANGO_SUPERUSER_PASSWORD** — your admin login; created automatically on first
  boot **with the Admin role**.
- Want demo data (sample mandis/commodities/users)? Set `SEED_DEMO` to `true`.

---

## 7. Mount the file share for media

1. Web App → **Settings → Configuration** → **Path mappings** tab →
   under **Azure Storage Mounts** click **+ New Azure Storage Mount**.
2. Fill in:
   - **Name:** `gvmedia`
   - **Configuration options:** Basic
   - **Storage accounts:** your `grainvisionst123`
   - **Storage type:** **Azure Files**
   - **Storage container / Share name:** `media`
   - **Mount path:** `/app/media`
3. **OK** → **Save**.

This keeps uploaded plate images across restarts and redeploys.

---

## 8. Restart, watch it boot, sign in

1. Web App → **Overview → Restart**.
2. Left menu → **Monitoring → Log stream**. The **first start pulls a multi-GB
   image**, so wait a few minutes. You'll see:
   `[entrypoint] applying database migrations…` then
   `[entrypoint] starting gunicorn…`.
3. Open `https://<your-app>.azurewebsites.net` and sign in:
   - **Username:** `admin`
   - **Password:** the `DJANGO_SUPERUSER_PASSWORD` you set
   - It logs in as **Admin** and shows the admin panels.
4. In the app: open **Mandis & commodities** to add your mandis/commodities, then
   **Users** to create assayer / QC / ML accounts and assign mandis to assayers.

You now have a full deployment. 🎉

---

## Updating after a code change

Because the ACR Task is wired to GitHub:
1. Commit your change to the `main` branch on GitHub (the commit trigger rebuilds
   automatically) — or open the registry → **Tasks → build-grainvision → Run**.
2. When the build shows **Succeeded**, go to the Web App → **Deployment Center**
   and **Sync**, or simply **Overview → Restart** to pull `:latest`.

---

## Notes & troubleshooting

- **Camera capture needs HTTPS.** `*.azurewebsites.net` is already HTTPS, so the
  capture viewfinder works in the browser once deployed.
- **App Service is CPU-only**, so SAM2 is slower than on a GPU. `SAM2_POINTS_PER_SIDE`
  is set to 16 to keep capture responsive. For GPU speed, see the GPU section in
  `DEPLOY_AZURE.md`.
- **"Application Error" right after deploy** → the first image pull is slow; open
  **Log stream** and wait for `starting gunicorn`. `WEBSITES_CONTAINER_START_TIME_LIMIT=1800`
  (already in the settings) gives it room.
- **Annotation image is blank** → re-check the **Path mapping** mount at
  `/app/media` and that `SERVE_MEDIA=True` is set.
- **`DisallowedHost` in the log** → `ALLOWED_HOSTS` must exactly match your host.
- **Can't pull image** → confirm the registry's **Admin user** is enabled
  (step 2.4); then Web App → **Deployment Center** → re-select the image and Save.
- **Change the admin password later** → sign in and use the **Users** panel.

---

## Simplest possible option (no SAM2, no Docker, no GitHub)

If you only want to validate the workflow quickly and cheaply, you can run it as a
plain Python App Service and skip SAM2 (it falls back to a classic CV engine):
create a **Web App** with **Publish = Code**, **Runtime = Python 3.12**, deploy the
project as a **ZIP** via **Deployment Center → ZIP Deploy**, and add the same
environment variables **except** set `SAM2_ENABLED=False` and remove the SAM2 ones.
This avoids the image build entirely. The container path above is the recommended,
full-fidelity deployment.
