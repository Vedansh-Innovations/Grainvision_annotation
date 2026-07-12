# GrainVision AI — Deployment Guide (DigitalOcean + Modal)

This guide deploys GrainVision as a **split system**:

- **DigitalOcean** runs everything on CPU: the Django web app, PostgreSQL
  database, image storage (Spaces), and the cheap OpenCV fallback engine.
- **Modal** runs **only** the SAM2 image segmentation on a serverless GPU
  that scales to zero — you pay per second, nothing while idle.

```
   Assayer's phone
        |  (capture plate)
        v
+-----------------------------+        HTTPS /segment        +--------------------+
|  DigitalOcean Droplet (CPU) | ---------------------------> |  Modal (GPU, L4)   |
|  Django + gunicorn + nginx  | <--------------------------- |  SAM2 worker       |
|                             |        polygons JSON         |  scales to zero    |
|  |- Managed PostgreSQL  ----+--> (database)                +--------------------+
|  |- Spaces (S3)  -----------+--> (plate images + exports)
+-----------------------------+
```

If Modal is ever unreachable, the app automatically falls back to the CPU
OpenCV engine, so captures never hard-fail.

**You will do four things, in this order:**
1. Deploy the GPU worker to Modal -> get a URL + token.
2. Create a PostgreSQL database on DigitalOcean.
3. Create a Spaces bucket for images on DigitalOcean.
4. Deploy the Django app on a DigitalOcean Droplet, pointing it at all three.

---

## 0. Before you start

You need:
- The project code in a **GitHub repo** (push this folder there).
- A **DigitalOcean** account (new accounts get $200 credit for 60 days).
- A **Modal** account (free Starter tier: $30/month credit, no card).
- A password manager or notepad to hold the secrets you'll generate.

Generate two secrets now and keep them:
```bash
# Django secret key (50+ chars)
python -c "import secrets; print(secrets.token_urlsafe(50))"
# Worker token (shared between the app and the GPU worker)
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## PART 1 - GPU segmentation worker on Modal

The worker code already exists in `worker/`, and `modal_app.py` wraps it for
Modal. You only deploy and configure it.

**1.1 Install Modal and log in** (on your laptop):
```bash
pip install modal
modal token new        # opens a browser to authenticate
```

**1.2 Store the worker token as a Modal secret** (use the WORKER_TOKEN you
generated in step 0):
```bash
modal secret create grainvision-worker WORKER_TOKEN=<your-worker-token>
```

**1.3 Deploy** (run from the project root, where `modal_app.py` is):
```bash
modal deploy modal_app.py
```
Modal builds the image (downloads SAM2 + the model - first build takes a few
minutes) and prints a public URL, for example:
```
https://<your-workspace>--grainvision-seg-fastapi.modal.run
```
**Copy that URL.** This is your `SAM2_REMOTE_URL`. Do **not** add a trailing
slash.

**1.4 Test it:**
```bash
curl https://<your-workspace>--grainvision-seg-fastapi.modal.run/health
# -> {"status":"ok","device":"cuda","cuda_available":true, ... }
```
The first call is a cold start (~10-20 s while the container + model load);
after that it's fast, and it scales back to zero 5 minutes after the last
request.

**1.5 Sensitivity ("detect the slightest part").**
It's already set high in `modal_app.py` (`SAM2_POINTS_PER_SIDE=64`, crop
layers, `SAM2_TILE_SIZE=1024`). To go higher, raise `SAM2_POINTS_PER_SIDE`
(e.g. 96) and/or `SAM2_CROP_N_LAYERS` to 2, then `modal deploy` again. Higher
= catches smaller specks but costs a bit more time/money per plate. If plates
ever hit the 600 s timeout, lower these.

---

## PART 2 - PostgreSQL database (DigitalOcean Managed Database)

**2.1** In the DigitalOcean console: **Create -> Databases -> PostgreSQL** (a
1 GB / smallest plan is fine to start). Put it in the **same region** as the
Droplet you'll create in Part 4.

**2.2** When it's ready, open the database -> **Connection details** and note:
`host`, `port` (usually 25060), `database`, `user`, `password`. Managed
Postgres **requires SSL**.

**2.3** Add your Droplet to the database's **Trusted Sources** once you have
it (Part 4) so only it can connect.

You'll put these into the app's `.env` as:
```
USE_POSTGRES=True
POSTGRES_DB=defaultdb
POSTGRES_USER=doadmin
POSTGRES_PASSWORD=<from console>
POSTGRES_HOST=<db-host>.db.ondigitalocean.com
POSTGRES_PORT=25060
POSTGRES_SSLMODE=require
```

---

## PART 3 - Image storage (DigitalOcean Spaces, S3-compatible)

Plate images and dataset exports are stored here instead of on the Droplet
disk, so they're durable and the disk never fills up.

**3.1** In the console: **Create -> Spaces Object Storage**. Choose a region,
name the bucket (e.g. `grainvision`), and set file listing to **Restricted**
(private).

**3.2** Create access keys: **API -> Spaces Keys -> Generate New Key**. Note
the **access key** and **secret**.

**3.3** Note your Spaces **endpoint**:
`https://<region>.digitaloceanspaces.com` (e.g. `https://blr1.digitaloceanspaces.com`).

You'll put these into `.env` as:
```
USE_S3=True
S3_ENDPOINT_URL=https://<region>.digitaloceanspaces.com
S3_ACCESS_KEY=<spaces access key>
S3_SECRET_KEY=<spaces secret>
S3_BUCKET=grainvision
```
The app serves images through **15-minute pre-signed URLs**, so the bucket
stays private. No public-read needed.

---

## PART 4 - Web app on a DigitalOcean Droplet (CPU)

**4.1 Create the Droplet:** **Create -> Droplets -> Ubuntu 24.04**. A
**CPU-Optimized 2 vCPU / 4 GB** (or Basic 2 vCPU / 4 GB) is enough to start.
Same region as the database. Add your SSH key. Note the Droplet's public IP.

**4.2** Add the Droplet's IP to the **database's Trusted Sources** (Part 2.3).

**4.3 SSH in and install system packages:**
```bash
ssh root@<droplet-ip>
apt update && apt install -y python3-venv python3-pip nginx git
```

**4.4 Get the code and create a virtualenv:**
```bash
cd /opt
git clone https://github.com/<you>/<your-repo>.git grainvision
cd grainvision
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```
> This installs the CPU/web dependencies. The line
> `git+https://github.com/facebookresearch/sam2.git@main` in
> `requirements.txt` is only needed if you ever want SAM2 to run *inside*
> this box. Since segmentation runs on Modal, you can delete that one line
> from `requirements.txt` before installing for a much smaller/faster build.

**4.5 Create the `.env` file** (`cp .env.example .env` then edit). Fill in
everything from Parts 1-3:
```ini
# Core
DEBUG=False
SECRET_KEY=<your django secret key>
ALLOWED_HOSTS=<your-domain-or-droplet-ip>
CSRF_TRUSTED_ORIGINS=https://<your-domain>

# Database (Part 2)
USE_POSTGRES=True
POSTGRES_DB=defaultdb
POSTGRES_USER=doadmin
POSTGRES_PASSWORD=<db password>
POSTGRES_HOST=<db-host>.db.ondigitalocean.com
POSTGRES_PORT=25060
POSTGRES_SSLMODE=require

# Image storage (Part 3)
USE_S3=True
S3_ENDPOINT_URL=https://<region>.digitaloceanspaces.com
S3_ACCESS_KEY=<spaces access key>
S3_SECRET_KEY=<spaces secret>
S3_BUCKET=grainvision

# Segmentation -> Modal GPU worker (Part 1). This is the whole "GPU only for
# segmentation" wiring: the app offloads segmentation and does nothing else
# on GPU. Leave SAM2_ENABLED=False so it never tries to load SAM2 locally.
SAM2_ENABLED=False
SAM2_REQUIRED=False
SAM2_REMOTE_URL=https://<your-workspace>--grainvision-seg-fastapi.modal.run
SAM2_REMOTE_TOKEN=<your worker token>
SAM2_REMOTE_TIMEOUT=180
```

**4.6 Initialise the app:**
```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py ensure_admin        # creates the first admin (see its output)
python manage.py seed_demo           # OPTIONAL: demo mandis/users to explore
```

**4.7 Run it with gunicorn under systemd.** Create
`/etc/systemd/system/grainvision.service`:
```ini
[Unit]
Description=GrainVision (gunicorn)
After=network.target

[Service]
WorkingDirectory=/opt/grainvision
EnvironmentFile=/opt/grainvision/.env
ExecStart=/opt/grainvision/.venv/bin/gunicorn grainvision.wsgi:application --workers 3 --timeout 120 --bind 127.0.0.1:8000
Restart=always

[Install]
WantedBy=multi-user.target
```
Then:
```bash
systemctl enable --now grainvision
systemctl status grainvision      # should be "active (running)"
```

**4.8 Put nginx in front.** Create `/etc/nginx/sites-available/grainvision`:
```nginx
server {
    listen 80;
    server_name <your-domain-or-ip>;
    client_max_body_size 25M;          # plate photos

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
```bash
ln -s /etc/nginx/sites-available/grainvision /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

**4.9 Add HTTPS** (required - the capture screen uses the phone camera, which
browsers only allow over HTTPS). Point a domain's A-record at the Droplet IP,
then:
```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d <your-domain>
```
Certbot rewrites the nginx config for TLS. After it runs, set
`CSRF_TRUSTED_ORIGINS=https://<your-domain>` in `.env` and
`systemctl restart grainvision`.

---

## PART 5 - Verify the whole chain

1. Open `https://<your-domain>` and sign in as the admin from step 4.6.
2. As admin, create a **Mandi** and tick its **commodities**
   (Mandis & commodities), and create an **Assayer** user assigned to that
   mandi (Users).
3. Sign in as the assayer on a **phone**, start a new sample, and **capture**
   a plate.
4. Confirm segmentation ran on the GPU: watch Modal's dashboard - you'll see
   a container spin up for the capture, then scale to zero ~5 min later.
5. Check the plate image appears in your **Spaces** bucket.

If a capture ever returns few/blobby grains, that's the **CPU fallback** -
it means the app couldn't reach Modal. Re-check `SAM2_REMOTE_URL`,
`SAM2_REMOTE_TOKEN`, and that the Modal app is deployed.

---

## PART 6 - Costs & tuning

- **Modal:** billed per second, scales to zero. At high sensitivity on an L4,
  a plate runs in the tens of seconds ~ a fraction of a cent. The **$30/month
  free credit** covers roughly a few thousand plates/month - likely all of
  your volume. Keep it warm during work hours by raising `scaledown_window`
  in `modal_app.py` if you want to avoid cold starts.
- **DigitalOcean:** Droplet (~$24/mo for 2 vCPU/4 GB), Managed Postgres
  (~$15/mo smallest), Spaces (~$5/mo). All CPU - cheap and steady.
- **Do NOT** run the GPU on a DigitalOcean **GPU Droplet** for this: those
  bill 24/7 even when powered off, so an idle-most-of-the-day segmentation
  GPU there is far more expensive than Modal's scale-to-zero.

**Sensitivity dial** (in `modal_app.py`, then `modal deploy`):
`SAM2_POINTS_PER_SIDE` up and `SAM2_CROP_N_LAYERS`=2 catch smaller specks but
cost more time per plate; `SAM2_TILE_SIZE` splits big plates so nothing is
missed. Lower them if you hit the timeout or want cheaper/faster captures.

---

## Environment variable reference

| Variable | Where | Purpose |
|---|---|---|
| `SECRET_KEY` | app | Django crypto key |
| `DEBUG` | app | `False` in production |
| `ALLOWED_HOSTS` | app | your domain / IP |
| `CSRF_TRUSTED_ORIGINS` | app | `https://<domain>` |
| `USE_POSTGRES` + `POSTGRES_*` | app | Managed Postgres connection |
| `USE_S3` + `S3_*` | app | Spaces bucket for images/exports |
| `SAM2_ENABLED=False` | app | never load SAM2 locally |
| `SAM2_REMOTE_URL` | app | Modal worker URL (no trailing slash) |
| `SAM2_REMOTE_TOKEN` | app | must equal the worker's `WORKER_TOKEN` |
| `SAM2_REMOTE_TIMEOUT` | app | seconds to wait for the GPU (e.g. 180) |
| `WORKER_TOKEN` | Modal secret | shared auth token for `/segment` |
| `SAM2_POINTS_PER_SIDE`, `SAM2_CROP_N_LAYERS`, `SAM2_TILE_SIZE` | Modal (`modal_app.py`) | segmentation sensitivity |

---

## Troubleshooting

- **Camera won't open on the capture screen** -> the site must be **HTTPS**
  (Part 4.9). Over plain HTTP, browsers block the camera.
- **`DisallowedHost` error** -> add your domain/IP to `ALLOWED_HOSTS`,
  restart gunicorn.
- **CSRF 403 on login** -> set `CSRF_TRUSTED_ORIGINS=https://<domain>`.
- **DB connection refused** -> add the Droplet IP to the database's Trusted
  Sources, and confirm `POSTGRES_SSLMODE=require`.
- **Images 403 / not showing** -> check the Spaces keys and `S3_BUCKET`; the
  app uses pre-signed URLs, so the bucket should stay private.
- **Every capture is blobby (CPU fallback)** -> the app can't reach Modal:
  verify `SAM2_REMOTE_URL`/`SAM2_REMOTE_TOKEN` and `modal deploy` status.
- **First capture of the day is slow** -> Modal cold start; raise
  `scaledown_window` in `modal_app.py` to keep a warm container during work
  hours.

---

### Alternative: DigitalOcean App Platform (instead of a Droplet)

If you'd rather not manage a server: push the repo, then **Create -> Apps**,
point it at the repo, set the **run command** to
`gunicorn grainvision.wsgi:application --workers 3 --timeout 120`, add all the
`.env` values as **App-Level Environment Variables**, and add a **pre-deploy
Job** running `python manage.py migrate`. Use the same Managed Postgres,
Spaces, and Modal settings. App Platform handles TLS automatically.
