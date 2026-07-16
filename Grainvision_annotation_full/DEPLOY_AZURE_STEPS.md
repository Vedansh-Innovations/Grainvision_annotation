# GrainVision on Azure — exact click-by-click guide (per-grain, cheap)

This walks you through **every page and button**. Follow it top to bottom.

**How to read the steps:**
- **SEARCH "X"** = click the search bar at the very top-center of the Azure
  portal, type X, and click the matching result.
- **Left menu → "Y"** = the vertical menu on the left side of the page you're on.
- **Click "Z"** = a button on the page.
- Anything in `code font` is a value to type. Replace `<...>` with your own.

You'll build two small images and seven Azure things. Keep these handy as you go
(write them in a notepad): a **SECRET_KEY**, a **DB password**, an **admin
password**, and a **WORKER_TOKEN** (each just a long random string you invent).

Use the region **Sweden Central** for everything (it's where the cheap GPU lives).

---

# PART 1 — Put the code on GitHub  (~5 min)

1. Go to **github.com**, sign in (or sign up — free).
2. Top-right, click **+** → **New repository**.
3. Repository name: `grainvision`. Choose **Private**. Click **Create repository**.
4. On the next page click the link **uploading an existing file**.
5. Open the `grainvision` project folder on your computer, select **all files
   inside it** (including `Dockerfile`, `Dockerfile.web`, `Dockerfile.worker`, and
   the `worker` folder), and drag them into the browser. Click **Commit changes**.
6. Make an access token so Azure can read the repo:
   - Top-right, click your **avatar** → **Settings**.
   - Bottom of the left menu → **Developer settings**.
   - **Personal access tokens** → **Tokens (classic)** → **Generate new token** →
     **Generate new token (classic)**.
   - Note: `azure`. Tick the **repo** checkbox. Click **Generate token**.
   - **Copy the token** and paste it in your notepad (you'll use it twice).

---

# PART 2 — Turn on GPU quota  (do this early; approval can take a few hours)

1. In the Azure portal, **SEARCH "Quotas"** → open **Quotas**.
2. Left menu → **Container Apps** (if present). If you can already see a row for a
   **T4 GPU** workload with a limit ≥ 1, you're done — skip to Part 3.
3. If not, request it: **SEARCH "Help + support"** → **Create a support request**.
   - Issue type: **Service and subscription limits (quotas)**.
   - Subscription: your subscription.
   - Quota type: **Managed Environment Consumption T4 GPUs**.
   - Click **Next**, **Enter details**, set the new limit to `1`, submit.
4. Wait for the email that says it's approved before doing Part 6. (Everything else
   below can be done while you wait.)

---

# PART 3 — Resource group  (~1 min)

1. **SEARCH "Resource groups"** → click **Resource groups**.
2. Click **+ Create** (top-left).
3. Resource group: `grainvision-rg`. Region: **Sweden Central**.
4. Click **Review + create** → **Create**.

---

# PART 4 — Container Registry + build the 2 images  (~25 min, mostly waiting)

### 4a. Create the registry
1. **SEARCH "Container registries"** → click it → **+ Create**.
2. Resource group: `grainvision-rg`.
3. Registry name: `grainvisionacr<your-initials>` (letters/numbers only, must be
   globally unique).
4. Location: **Sweden Central**. SKU: **Basic**.
5. **Review + create** → **Create**. When done, click **Go to resource**.
6. Left menu → **Access keys**. Toggle **Admin user** to **Enabled**.

### 4b. Build the WEB image
7. Left menu → **Tasks** (under "Services") → **+ Add**.
8. Fill in:
   - Task name: `build-web`
   - Image: `grainvision-web:latest`
   - Dockerfile: `Dockerfile.web`
   - Source control: **GitHub**
   - Repository URL: `https://github.com/<you>/grainvision.git`
   - Branch: `main`
   - Under authentication, paste your **GitHub token**.
9. Click **Create**. Click the new **build-web** task → **Run** (top) → confirm.
10. It runs for a few minutes. Wait for **Succeeded**.

### 4c. Build the WORKER image (the GPU one)
11. **Tasks** → **+ Add** again:
    - Task name: `build-worker`
    - Image: `grainvision-worker:latest`
    - Dockerfile: `Dockerfile.worker`
    - Same GitHub repo / branch / token.
12. **Create** → open **build-worker** → **Run**.
13. This one is big (CUDA + SAM2) — **wait ~15–20 min** for **Succeeded**.
14. Confirm both images exist: left menu → **Repositories** → you should see
    `grainvision-web` and `grainvision-worker`.

---

# PART 5 — PostgreSQL database  (~10 min)

1. **SEARCH "Azure Database for PostgreSQL flexible servers"** → click it →
   **+ Create** (choose **Flexible server** if asked).
2. Basics:
   - Resource group: `grainvision-rg`
   - Server name: `grainvision-pg-<your-initials>` (globally unique)
   - Region: **Sweden Central**
   - PostgreSQL version: **16**
   - Workload type: **Development** (this picks the cheap Burstable B1ms)
   - Authentication method: **PostgreSQL authentication only**
   - Admin username: `gvadmin`
   - Password: your **DB password** (from your notepad)
3. Click the **Networking** tab:
   - Connectivity method: **Public access (allowed IP addresses)**
   - Tick **Allow public access from any Azure service within Azure to this server**.
4. **Review + create** → **Create**. Wait, then **Go to resource**.
5. Left menu → **Databases** → **+ Add** → name `grainvision` → **Save**.
6. Left menu → **Overview** → copy the **Server name**
   (e.g. `grainvision-pg-xx.postgres.database.azure.com`) into your notepad.

---

# PART 6 — Storage account + file share (stores the plate images)  (~5 min)

1. **SEARCH "Storage accounts"** → click it → **+ Create**.
2. Resource group: `grainvision-rg`. Name: `grainvisionst<initials>` (lowercase
   letters/numbers, unique). Region: **Sweden Central**. Redundancy: **LRS**.
3. **Review + create** → **Create** → **Go to resource**.
4. Left menu → **File shares** (under "Data storage") → **+ File share** →
   Name: `media` → **Create**.
5. Left menu → **Access keys** (under "Security + networking") → **Show** on key1 →
   copy the **Key** value into your notepad.

---

# PART 7 — The GPU worker (only runs while segmenting)  (~10 min)

*(Do this after the GPU quota from Part 2 is approved.)*

### 7a. Create the Container Apps environment
1. **SEARCH "Container Apps Environments"** → click it → **+ Create**.
2. Resource group: `grainvision-rg`. Environment name: `grainvision-env`.
   Region: **Sweden Central**.
3. **Review + create** → **Create** → **Go to resource**.

### 7b. Add the GPU workload profile
4. In the environment, left menu → **Workload profiles** → **+ Add**.
5. Choose the GPU profile (named **Consumption GPU / NVIDIA T4 / NC8as-T4**).
   Give it the name `gpu`. Click **Add**, then **Save** / **Deploy**.
   - If no GPU option appears, your quota (Part 2) isn't approved yet — wait.

### 7c. Create the worker app
6. **SEARCH "Container Apps"** → click it → **+ Create**.
7. Basics:
   - Resource group: `grainvision-rg`
   - Container app name: `grainvision-worker`
   - Region: **Sweden Central**
   - Container Apps Environment: **grainvision-env** (the one you just made)
8. Click the **Container** tab:
   - **Uncheck** "Use quickstart image".
   - Image source: **Azure Container Registry**.
   - Registry: your `grainvisionacr…`. Image: `grainvision-worker`. Tag: `latest`.
   - **Workload profile**: select **gpu**.
   - Scroll to **Environment variables** → **+ Add** one row each:
     | Name | Value |
     |---|---|
     | `SAM2_DEVICE` | `cuda` |
     | `SAM2_TILES` | `True` |
     | `SAM2_POINTS_PER_SIDE` | `64` |
     | `SAM2_CROP_N_LAYERS` | `1` |
     | `SAM2_TILE_SIZE` | `0` |
     | `WORKER_TOKEN` | `<your worker token>` |
9. Click the **Ingress** tab:
   - **Enable** Ingress.
   - Ingress traffic: **Accepting traffic from anywhere**.
   - Target port: `8080`.
10. **Review + create** → **Create** → **Go to resource**.
11. Left menu → **Scale** (or **Scale and replicas**) → set **Min replicas = 0**,
    **Max replicas = 1** → **Save**.
12. Left menu → **Overview** → copy the **Application URL**
    (e.g. `https://grainvision-worker.<hash>.swedencentral.azurecontainerapps.io`)
    into your notepad.
13. Test it: open that URL with `/health` added at the end in a new browser tab.
    The first hit wakes the GPU (wait ~1–2 min); it should then show
    `"cuda_available": true`.

---

# PART 8 — The web app (the site people use)  (~10 min)

### 8a. Create it
1. **SEARCH "App Services"** → click it → **+ Create** → **Web App**.
2. Basics:
   - Resource group: `grainvision-rg`
   - Name: `grainvision-app-<initials>` → your site will be
     `https://grainvision-app-<initials>.azurewebsites.net`
   - Publish: **Container**
   - Operating System: **Linux**
   - Region: **Sweden Central**
   - Linux Plan → **Create new** → then **Pricing plan** → **Basic B1**.
3. Click the **Container** tab:
   - Image Source: **Azure Container Registry**
   - Registry: your `grainvisionacr…`. Image: `grainvision-web`. Tag: `latest`.
4. **Review + create** → **Create** → **Go to resource**.

### 8b. Add the settings
5. Left menu → **Environment variables** (under "Settings"; on some portals it's
   **Configuration → Application settings**).
6. Click **Advanced edit**. Delete what's there, paste the block below, and fill in
   every `<...>` from your notepad. Click **OK**, then **Apply** → **Confirm**.

```json
[
  { "name": "WEBSITES_PORT", "value": "8000" },
  { "name": "WEBSITES_ENABLE_APP_SERVICE_STORAGE", "value": "false" },
  { "name": "WEBSITES_CONTAINER_START_TIME_LIMIT", "value": "600" },
  { "name": "DEBUG", "value": "False" },
  { "name": "SECRET_KEY", "value": "<your SECRET_KEY>" },
  { "name": "ALLOWED_HOSTS", "value": "grainvision-app-<initials>.azurewebsites.net" },
  { "name": "CSRF_TRUSTED_ORIGINS", "value": "https://grainvision-app-<initials>.azurewebsites.net" },
  { "name": "SECURE_SSL_REDIRECT", "value": "True" },
  { "name": "USE_POSTGRES", "value": "True" },
  { "name": "POSTGRES_DB", "value": "grainvision" },
  { "name": "POSTGRES_USER", "value": "gvadmin" },
  { "name": "POSTGRES_PASSWORD", "value": "<your DB password>" },
  { "name": "POSTGRES_HOST", "value": "<your pg server name>.postgres.database.azure.com" },
  { "name": "POSTGRES_PORT", "value": "5432" },
  { "name": "POSTGRES_SSLMODE", "value": "require" },
  { "name": "USE_S3", "value": "False" },
  { "name": "SERVE_MEDIA", "value": "True" },
  { "name": "SAM2_ENABLED", "value": "False" },
  { "name": "SAM2_REMOTE_URL", "value": "<your worker Application URL>" },
  { "name": "SAM2_REMOTE_TOKEN", "value": "<your worker token>" },
  { "name": "SAM2_REMOTE_TIMEOUT", "value": "180" },
  { "name": "GUNICORN_WORKERS", "value": "2" },
  { "name": "GUNICORN_TIMEOUT", "value": "300" },
  { "name": "DJANGO_SUPERUSER_USERNAME", "value": "admin" },
  { "name": "DJANGO_SUPERUSER_PASSWORD", "value": "<your admin password>" },
  { "name": "DJANGO_SUPERUSER_EMAIL", "value": "admin@example.com" },
  { "name": "SEED_DEMO", "value": "false" }
]
```

### 8c. Mount the file share (so uploaded images persist)
7. Left menu → **Configuration** → top tab **Path mappings** → under **Azure
   storage mounts** click **+ New Azure storage mount**.
8. Fill in:
   - Name: `gvmedia`
   - Storage account: your `grainvisionst…`
   - Storage type: **Azure Files**
   - Share name: `media`
   - Mount path: `/app/media`
9. Click **OK** → **Save**.

### 8d. Start it
10. Left menu → **Overview** → **Restart**.
11. Left menu → **Log stream** (under "Monitoring"). Wait until you see
    `[entrypoint] starting gunicorn`. (First start pulls the image — a few minutes.)

---

# PART 9 — Sign in and test per-grain

1. Open `https://grainvision-app-<initials>.azurewebsites.net`.
2. Sign in: username `admin`, password = your **admin password**. It logs in as
   **Admin**.
3. Left menu in the app → **Mandis & commodities** → add a mandi and a commodity.
   Then **Users** → create an assayer and assign the mandi.
4. Start a sample and **capture a plate**. The **first** capture wakes the GPU
   worker (wait ~1–3 min); after that captures take seconds.
5. On the annotation screen you'll see **individual grain outlines**, including
   inside the dense clusters — that's the tiled SAM2 on the GPU.
6. The worker automatically scales back to zero a few minutes after the last
   capture, so you stop paying for the GPU.

---

# Money & upkeep

- **Web app B1 ~$13/mo + Postgres B1ms ~$13/mo** are the only always-on costs.
  Stop the Postgres server from its **Overview → Stop** when nobody's working to
  save more.
- **GPU costs only while segmenting** and is **$0 when idle** (scales to zero).
- **First capture after idle is slow** (GPU cold start). If that bothers your team,
  open the worker → **Scale** → set **Min replicas = 1** during work hours (you pay
  for the GPU while it's on), and back to `0` afterwards.
- **After you change code:** push to GitHub → registry **Tasks → Run** the relevant
  build → then **Restart** the web app (or, for the worker, it picks up `latest` on
  its next cold start; to force it, open the worker and **Restart**).

---

# If something's wrong

| What you see | Where to look / fix |
|---|---|
| Site shows "Application Error" | Web app → **Log stream**; first boot is slow, wait for `starting gunicorn`. |
| Login works but capture spins forever | Worker still cold-starting, or `SAM2_REMOTE_URL` / `SAM2_REMOTE_TOKEN` wrong. Open the worker `/health`; check the two values match your notepad. |
| Annotation image is blank | Web app → **Configuration → Path mappings**: the `/app/media` mount must be there; `SERVE_MEDIA=True`. |
| Worker has no GPU option (Part 7b) | GPU quota (Part 2) not approved yet. |
| "DisallowedHost" in the log | `ALLOWED_HOSTS` must exactly equal your `…azurewebsites.net` host. |
| Capture returns an error about SAM2 unavailable | Confirm the worker image built (Part 4c) and its app is running; re-open `/health`. |
