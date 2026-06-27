# Deploying GrainVision AI to Azure

This guide gives you a **full, working deployment** on Azure. There are two paths:

- **Path A — one script (recommended).** Run `azure/deploy.sh`; it creates
  everything and prints your URL and admin password.
- **Path B — manual, step by step.** The same actions as individual commands, if
  you prefer to run them yourself or adapt them.

Everything is scripted. Your only manual work is: install the Azure CLI, log in,
and run one script (Path A) or paste the commands (Path B).

---

## What gets created

```
Resource group
├── Container Registry (ACR)          ← builds & stores the Docker image
├── PostgreSQL Flexible Server        ← the database (db: grainvision)
├── Storage account + Files share     ← persistent media (plate images)
└── App Service plan (Linux) + Web App for Containers
                                       ← runs the container, public HTTPS URL
```

Architecture: the container runs Django under gunicorn with WhiteNoise for
static files. Uploaded plate images live on an Azure Files share mounted at
`/app/media` (so they survive restarts/redeploys). SAM2 runs on CPU inside the
container with the checkpoint baked into the image.

> **Cost note.** Defaults are modest but **not free**: App Service `P1v3`, a
> Burstable `B1ms` Postgres, a Basic ACR, and an LRS storage account. Expect a
> small daily cost. Run `azure/teardown.sh` to delete everything when done.

> **Performance note.** App Service is **CPU-only**, so SAM2 is slower than on a
> GPU. The deploy sets `SAM2_POINTS_PER_SIDE=16` to keep capture latency
> reasonable. For production-grade speed see **GPU options** below.

---

## Prerequisites (one time, manual)

1. **Install the Azure CLI** (version ≥ 2.50):
   https://learn.microsoft.com/cli/azure/install-azure-cli
2. **Log in and pick your subscription:**
   ```bash
   az login
   az account set --subscription "<YOUR SUBSCRIPTION NAME OR ID>"
   ```
3. **Have the project on your machine** and `cd` into the project root (the
   folder that contains `Dockerfile`).

You do **not** need Docker installed locally — the image is built in the cloud by
ACR.

---

## Path A — deploy with one script

From the project root:

```bash
bash azure/deploy.sh
```

Optional overrides (export before running):

```bash
export LOCATION=eastus          # default: centralindia
export PREFIX=grainvision       # name prefix for all resources
export PLAN_SKU=P1v3            # B2 is the cheapest that fits torch/SAM2
export ADMIN_USER=admin
export ADMIN_PASSWORD='Choose-A-Strong-One!'   # else one is generated
bash azure/deploy.sh
```

The script runs nine steps (group → ACR + cloud build → Postgres → storage →
plan → web app → settings → media mount → restart). The **image build takes
10–20 minutes** the first time because it compiles PyTorch + SAM2.

When it finishes it prints:

```
DEPLOYED ✓   https://grainvision-xxxxxx.azurewebsites.net
 Admin login:  username / password / role=Admin
 Postgres admin password: ...
```

**Copy those credentials immediately** — they are not stored anywhere else.

### First boot

The first container start pulls a multi-GB image and runs migrations, so give it
a few minutes. Watch it live:

```bash
az webapp log tail -g grainvision-rg -n <APP>
```

You'll see `[entrypoint] applying database migrations…` then
`[entrypoint] starting gunicorn…`. Once gunicorn is up, open the URL and sign in
with the admin account. The admin is created automatically and **has the Admin
role** (this is the `createsuperuser` role fix).

That's it — you have a full deployment.

---

## Path B — manual, step by step

If you'd rather run each piece yourself, these are the same actions. Set a few
variables first (keep names globally unique):

```bash
RG=grainvision-rg; LOCATION=centralindia
ACR=grainvisionacr$RANDOM; PLAN=grainvision-plan; APP=grainvision-$RANDOM
PG=grainvision-pg-$RANDOM; PG_DB=grainvision; PG_ADMIN=gvadmin
STORAGE=grainvisionst$RANDOM; SHARE=media
PG_PASSWORD='Strong-Pg-Pass1!'; SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(50))')"
ADMIN_USER=admin; ADMIN_PASSWORD='Strong-Admin-Pass1!'
IMAGE=grainvision:latest; HOST=$APP.azurewebsites.net
```

**1. Resource group**
```bash
az group create -n $RG -l $LOCATION
```

**2. Registry + build the image in the cloud**
```bash
az acr create -g $RG -n $ACR --sku Basic --admin-enabled true
az acr build --registry $ACR --image $IMAGE --file Dockerfile .
ACR_SERVER=$(az acr show -n $ACR -g $RG --query loginServer -o tsv)
ACR_USER=$(az acr credential show -n $ACR -g $RG --query username -o tsv)
ACR_PASS=$(az acr credential show -n $ACR -g $RG --query 'passwords[0].value' -o tsv)
```

**3. PostgreSQL**
```bash
az postgres flexible-server create -g $RG -n $PG -l $LOCATION \
  --tier Burstable --sku-name Standard_B1ms \
  --admin-user $PG_ADMIN --admin-password "$PG_PASSWORD" \
  --storage-size 32 --version 16 --public-access 0.0.0.0 --yes
az postgres flexible-server db create -g $RG -s $PG -d $PG_DB
az postgres flexible-server firewall-rule create -g $RG -n $PG \
  --rule-name allow-azure --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0
```

**4. Storage + Files share (media)**
```bash
az storage account create -g $RG -n $STORAGE -l $LOCATION --sku Standard_LRS --kind StorageV2
STORAGE_KEY=$(az storage account keys list -g $RG -n $STORAGE --query '[0].value' -o tsv)
az storage share-rm create -g $RG --storage-account $STORAGE -n $SHARE --quota 50
```

**5. Plan + web app**
```bash
az appservice plan create -g $RG -n $PLAN --is-linux --sku P1v3
az webapp create -g $RG -p $PLAN -n $APP --deployment-container-image-name $ACR_SERVER/$IMAGE
az webapp config container set -g $RG -n $APP \
  --container-image-name $ACR_SERVER/$IMAGE \
  --container-registry-url https://$ACR_SERVER \
  --container-registry-user $ACR_USER \
  --container-registry-password "$ACR_PASS"
```

**6. App settings** (see `azure/app-settings.example.env` for the full list)
```bash
az webapp config appsettings set -g $RG -n $APP --settings \
  WEBSITES_PORT=8000 WEBSITES_ENABLE_APP_SERVICE_STORAGE=false \
  DEBUG=False SECRET_KEY="$SECRET_KEY" \
  ALLOWED_HOSTS="$HOST" CSRF_TRUSTED_ORIGINS="https://$HOST" SECURE_SSL_REDIRECT=True \
  USE_POSTGRES=True POSTGRES_DB=$PG_DB POSTGRES_USER=$PG_ADMIN \
  POSTGRES_PASSWORD="$PG_PASSWORD" POSTGRES_HOST=$PG.postgres.database.azure.com \
  POSTGRES_PORT=5432 POSTGRES_SSLMODE=require \
  USE_S3=False SERVE_MEDIA=True \
  SAM2_ENABLED=True SAM2_REQUIRED=True SAM2_DEVICE=cpu SAM2_POINTS_PER_SIDE=16 \
  GUNICORN_WORKERS=2 GUNICORN_TIMEOUT=300 \
  DJANGO_SUPERUSER_USERNAME=$ADMIN_USER DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASSWORD" \
  DJANGO_SUPERUSER_EMAIL=admin@example.com SEED_DEMO=false
```

**7. Mount media + restart**
```bash
az webapp config storage-account add -g $RG -n $APP --custom-id gvmedia \
  --storage-type AzureFiles --account-name $STORAGE --share-name $SHARE \
  --access-key "$STORAGE_KEY" --mount-path /app/media
az webapp restart -g $RG -n $APP
echo "https://$HOST"
```

---

## After deployment

- **Sign in** at `https://<APP>.azurewebsites.net` with the admin account. It has
  the **Admin** role and sees the admin panels (Dashboard, Users, Mandis &
  commodities, etc.).
- **Add mandis and commodities** under *Mandis & commodities*, then create
  assayer / QC / ML users under *Users* and assign mandis to assayers.
- **Change the admin password** anytime from the Users panel, or:
  ```bash
  az webapp ssh -g grainvision-rg -n <APP>
  # inside the container:
  python manage.py changepassword admin
  ```
- **Load demo data** instead of starting empty: set `SEED_DEMO=true` in app
  settings and restart (creates sample mandis, commodities and one user per role).

---

## Updating the app after code changes

```bash
export RG=grainvision-rg ACR=<your-acr-name> APP=<your-app-name>
bash azure/redeploy.sh
```

This rebuilds the image in ACR and restarts the web app.

---

## Custom domain + HTTPS (optional, manual)

App Service already serves HTTPS on `*.azurewebsites.net`. For your own domain:

```bash
az webapp config hostname add -g $RG --webapp-name $APP --hostname app.yourdomain.com
az webapp config ssl create -g $RG --name $APP --hostname app.yourdomain.com   # managed cert
```

Then add the new host to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` app settings
and restart.

---

## GPU options (for real segmentation speed)

App Service is CPU-only. For GPU-backed SAM2 you have two good options:

1. **Azure Container Apps with serverless GPU** (NVIDIA A100/T4 in supported
   regions). Push the same image to ACR, create a GPU-enabled Container App, and
   set `SAM2_DEVICE=cuda` plus `SAM2_POINTS_PER_SIDE=64`. You'll also want a CUDA
   base image — swap the Dockerfile's torch install to the `cu121` index and use
   an `nvidia/cuda` runtime base.
2. **A GPU VM** (e.g. `Standard_NC*`/`NC*ads` series) running the same container
   with `--gpus all`, fronted by nginx or Caddy for TLS.

On CPU, keep `SAM2_POINTS_PER_SIDE` at 12–16. You can also set
`SAM2_ENABLED=False` for a fast classical-CV fallback if you only need to
validate the workflow cheaply.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| App shows "Application Error" right after deploy | First start is slow (large image). `az webapp log tail -g $RG -n $APP` and wait for `starting gunicorn`. |
| Container starts then exits | Check logs for a migration/DB error. Confirm the Postgres `allow-azure` firewall rule exists and `POSTGRES_*` settings match. |
| Annotation image is blank | Confirm the media mount: `az webapp config storage-account list -g $RG -n $APP` should show `/app/media`. `SERVE_MEDIA=True` must be set. |
| `DisallowedHost` in logs | `ALLOWED_HOSTS` must equal your host; for a custom domain add it and restart. |
| Capture request times out | You're on CPU with too dense a grid. Lower `SAM2_POINTS_PER_SIDE` to 12, keep `GUNICORN_TIMEOUT=300`. |
| `az acr build` fails on memory | Retry; ACR build runs in the cloud. For very constrained quota, build locally with Docker and `az acr login` + `docker push`. |

---

## Teardown

Delete the whole resource group (everything above):

```bash
RG=grainvision-rg bash azure/teardown.sh
```
