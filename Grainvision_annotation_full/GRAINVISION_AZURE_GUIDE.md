# GrainVision — Deploy from GitHub (CPU, free) + add GPU later (any provider)

The **only** guide you need. Two stages:

- **Now (CPU, no Docker, no registry, no extra DB):** Azure builds and runs your
  Django code straight from GitHub on a small web app. Capture, measurements,
  annotation, QC, dashboards all work. Segmentation uses the fast built-in OpenCV
  engine. Separated grains segment cleanly; very dense clusters may merge (a person
  can split those in the canvas). Runs on your **Sponsorship credits** -> ~free.
- **Later (GPU, pay-per-use):** for true **per-grain**, run the worker container on
  ANY GPU host (AWS, DigitalOcean, RunPod, a local PC...) and set ONE variable on
  the web app. No re-deploy. Turn it off any time -> back to CPU.

---

## How to read this
- **SEARCH "X"** = the search bar at the top of the Azure portal -> type X -> click.
- **Left menu -> "Y"** = the menu on the left of the current page.
- `code` = a value to type; replace `<...>` with your own.

## Notepad (write these down now)
Invent two long random strings: `SECRET_KEY` and `ADMIN_PASSWORD`.
Use region **Sweden Central**. Pick your **Sponsorship** subscription on every
screen (no card needed for the CPU stage).

---

# STAGE 1 — CPU app from GitHub (do this now)

## PART 0 — Put the code on GitHub
1. github.com -> sign in -> **+ -> New repository** -> name `grainvision`,
   **Private** -> **Create repository**.
2. Click **uploading an existing file** -> drag in **all files inside** the project
   folder -> **Commit changes**.
   (No access token needed this time - Azure connects to GitHub by sign-in.)

## PART 1 — Create the Web App (Code, not container)
1. **SEARCH "App Services"** -> **+ Create -> Web App**.
2. Basics:
   - **Subscription = Sponsorship.** Resource group `grainvision_rg`.
   - Name `grainvision-app-<initials>` -> site will be
     `https://grainvision-app-<initials>.azurewebsites.net`.
   - **Publish: Code**  (NOT Container).
   - **Runtime stack: Python 3.12.**
   - Operating System **Linux**. Region **Sweden Central**.
   - Pricing plan -> **Create new** -> **Basic B1**.
3. **Review + create** -> **Create** -> **Go to resource**.

## PART 2 — Connect GitHub (this builds + deploys)
1. In the web app, left menu -> **Deployment Center**.
2. Source: **GitHub** -> **Authorize** (sign in to GitHub if asked, allow access).
3. Pick your **Organization** (your username), **Repository** `grainvision`,
   **Branch** `main`.
4. If it asks for a build provider, choose **App Service Build Service** (simplest).
5. Click **Save** at the top. It starts the first build/deploy.
6. Open the **Logs** tab and wait until the latest entry shows **Success**
   (a few minutes - it installs the Python packages).

## PART 3 — Startup command + settings
1. Left menu -> **Configuration** -> tab **General settings** -> **Startup Command**:
   type `bash startup.sh` -> **Save**.
2. Left menu -> **Environment variables** (or **Configuration -> Application
   settings**) -> **Advanced edit** -> paste this, fill in `<...>` -> **OK** ->
   **Apply** (it restarts):

```json
[
  { "name": "SECRET_KEY", "value": "<SECRET_KEY>" },
  { "name": "DEBUG", "value": "False" },
  { "name": "ALLOWED_HOSTS", "value": "grainvision-app-<initials>.azurewebsites.net" },
  { "name": "CSRF_TRUSTED_ORIGINS", "value": "https://grainvision-app-<initials>.azurewebsites.net" },
  { "name": "SECURE_SSL_REDIRECT", "value": "True" },
  { "name": "USE_POSTGRES", "value": "False" },
  { "name": "SQLITE_PATH", "value": "/home/data/grainvision.sqlite3" },
  { "name": "MEDIA_ROOT", "value": "/home/data/media" },
  { "name": "SERVE_MEDIA", "value": "True" },
  { "name": "SAM2_ENABLED", "value": "False" },
  { "name": "SAM2_REQUIRED", "value": "False" },
  { "name": "DJANGO_SUPERUSER_USERNAME", "value": "admin" },
  { "name": "DJANGO_SUPERUSER_PASSWORD", "value": "<ADMIN_PASSWORD>" },
  { "name": "DJANGO_SUPERUSER_EMAIL", "value": "admin@example.com" },
  { "name": "SCM_DO_BUILD_DURING_DEPLOYMENT", "value": "true" },
  { "name": "GUNICORN_WORKERS", "value": "2" },
  { "name": "GUNICORN_TIMEOUT", "value": "120" }
]
```
   - `USE_POSTGRES=False` -> uses a built-in file database (SQLite). No separate
     database resource to create or pay for.
   - `SQLITE_PATH` and `MEDIA_ROOT` point at `/home/data`, which **persists across
     restarts and redeploys**, so your data and uploads are safe.
   - The startup script (`startup.sh`) auto-runs the database setup, collects static
     files, and creates the `admin` user for you.

## PART 4 — Restart and open
1. Left menu -> **Overview -> Restart**.
2. Left menu -> **Log stream** -> wait for `starting gunicorn`.
3. Open `https://grainvision-app-<initials>.azurewebsites.net` -> sign in
   `admin` / `ADMIN_PASSWORD`.
4. **Mandis & commodities** -> add one of each. **Users** -> add an assayer.
5. Capture a plate -> it segments instantly on CPU (separated grains clean; dense
   clumps may merge - fine for checking the workflow).

**Cost of Stage 1:** just the B1 web app on your **Sponsorship credits** -> ~free.
No registry, no database server, no storage account.

> Note on scale: SQLite is perfect for checking and light single-team use. When you
> need many people writing at once in production, switch to PostgreSQL (set
> `USE_POSTGRES=True` + the `POSTGRES_*` variables). Ask me and I'll give those steps.

---

# STAGE 2 — Add a GPU later (any provider, pay-per-use)

Do this only when you want true per-grain segmentation. You run the **worker
container** (folder `worker/`, listens on **port 8080**) on a GPU box and point the
web app at it.

### Step A — Get the worker image onto the GPU host
- **Push to Docker Hub** (so any provider can pull): on a machine with Docker,
  `docker build -f Dockerfile.worker -t <dockerhubuser>/grainvision-worker .` then
  `docker push <dockerhubuser>/grainvision-worker`.
- **Or build on the GPU box:** clone the repo there and
  `docker build -f Dockerfile.worker -t grainvision-worker .`

### Step B — Run it (any Linux box with an NVIDIA GPU + Docker + drivers)
```bash
docker run -d --name grainvision-worker --restart unless-stopped --gpus all \
  -p 8080:8080 \
  -e SAM2_DEVICE=cuda -e SAM2_TILES=True -e SAM2_POINTS_PER_SIDE=64 \
  -e SAM2_CROP_N_LAYERS=1 -e SAM2_TILE_SIZE=0 \
  -e WORKER_TOKEN='<pick a long secret>' \
  <the image from Step A>
```
Open **port 8080** in the provider's firewall. Check:
`http://<host public IP>:8080/health` -> should say `"cuda_available": true`.

### Step C — Point the web app at it (one change, no redeploy)
Azure web app -> **Environment variables** -> add:
```
SAM2_REMOTE_URL    = http://<GPU host public IP>:8080
SAM2_REMOTE_TOKEN  = <the same WORKER_TOKEN from Step B>
SAM2_REMOTE_TIMEOUT= 180
```
**Apply -> Restart.** Captures now do per-grain on the GPU. To go back to CPU,
delete `SAM2_REMOTE_URL` and restart.

### Provider quick-notes (all "pay only while running")
- **RunPod** - easiest per-minute. Create a **Pod** from your image, expose HTTP
  port 8080, add the Step B env vars, use the pod URL as `SAM2_REMOTE_URL`.
- **DigitalOcean GPU Droplet** - drivers preinstalled; install Docker, run Step B,
  allow 8080 in the firewall. Hourly.
- **AWS EC2 g4dn.xlarge** (1x T4) - use the Deep Learning AMI (drivers + Docker),
  run Step B, open 8080 in the security group. Hourly.
- **Your own GPU PC** - run Step B, expose with a tunnel (e.g. `cloudflared`) and
  use that URL.

**Cost of Stage 2:** you pay the GPU provider only while that box is on; nothing
changes on Azure.

---

# If something's wrong
| What you see | Fix |
|---|---|
| Deployment "Failed" in Deployment Center | Open the failed log; usually a typo in a file. Re-commit and it redeploys. |
| Site "Application Error" | Web app -> **Log stream**; first boot is slow - wait for `starting gunicorn`. Confirm Startup Command is `bash startup.sh`. |
| "DisallowedHost" | `ALLOWED_HOSTS` must equal your exact `...azurewebsites.net` host. |
| Annotation image blank | `SERVE_MEDIA=True` must be set; `MEDIA_ROOT=/home/data/media`. |
| Data disappeared after a redeploy | `SQLITE_PATH` and `MEDIA_ROOT` must point under `/home/data` (not the default). |
| (Stage 2) capture fails | GPU box off, or `SAM2_REMOTE_URL`/`SAM2_REMOTE_TOKEN` wrong. Open `http://<GPU IP>:8080/health`. |
