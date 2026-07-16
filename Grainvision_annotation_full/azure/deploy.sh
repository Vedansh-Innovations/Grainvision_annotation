#!/usr/bin/env bash
###############################################################################
# GrainVision AI — one-shot Azure provisioning
#
# Creates EVERYTHING needed to run the app on Azure App Service for Containers:
#   • Resource group
#   • Azure Container Registry (ACR) + cloud image build (no local Docker needed)
#   • PostgreSQL Flexible Server + database
#   • Storage account + Azure Files share (persists uploaded plate images)
#   • Linux App Service plan + Web App for Containers
#   • All app settings (env vars), secrets, and the media file mount
#
# PREREQUISITES (manual, one time):
#   1. Install Azure CLI:  https://learn.microsoft.com/cli/azure/install-azure-cli
#   2. az login
#   3. az account set --subscription "<YOUR SUBSCRIPTION>"
#   4. Run this script FROM THE PROJECT ROOT (the folder containing Dockerfile):
#         bash azure/deploy.sh
#
# Re-running is safe-ish: most creates are idempotent. To change names/region,
# edit the variables below or pass them as environment variables.
###############################################################################
set -euo pipefail

# ── Configuration (override by exporting before running) ─────────────────────
PREFIX="${PREFIX:-grainvision}"
SUFFIX="${SUFFIX:-$(openssl rand -hex 3)}"          # keeps global names unique
LOCATION="${LOCATION:-centralindia}"               # e.g. eastus, westeurope

RG="${RG:-${PREFIX}-rg}"
ACR="${ACR:-${PREFIX}acr${SUFFIX}}"                 # 5-50 alphanumeric, globally unique
PLAN="${PLAN:-${PREFIX}-plan}"
APP="${APP:-${PREFIX}-${SUFFIX}}"                   # -> https://<APP>.azurewebsites.net
PG="${PG:-${PREFIX}-pg-${SUFFIX}}"                  # postgres server, globally unique
PG_DB="${PG_DB:-grainvision}"
PG_ADMIN="${PG_ADMIN:-gvadmin}"
STORAGE="${STORAGE:-${PREFIX}st${SUFFIX}}"          # 3-24 lowercase alnum, globally unique
SHARE="${SHARE:-media}"

IMAGE="grainvision:latest"
PLAN_SKU="${PLAN_SKU:-B2}"                            # cheapest tier that fits SAM2 on CPU
PG_SKU="${PG_SKU:-Standard_B1ms}"
PG_TIER="${PG_TIER:-Burstable}"

# Secrets (auto-generated if not provided)
SECRET_KEY="${SECRET_KEY:-$(python3 - <<'PY'
import secrets; print(secrets.token_urlsafe(50))
PY
)}"
PG_PASSWORD="${PG_PASSWORD:-$(openssl rand -base64 18 | tr -d '/+=' )Aa1!}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -base64 12 | tr -d '/+=' )Aa1!}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"

# SAM2 runtime (App Service is CPU-only). Lower point grid keeps latency sane.
SAM2_DEVICE="${SAM2_DEVICE:-cpu}"
SAM2_POINTS_PER_SIDE="${SAM2_POINTS_PER_SIDE:-12}"
SAM2_REQUIRED="${SAM2_REQUIRED:-True}"

HOST="${APP}.azurewebsites.net"

echo "──────────────────────────────────────────────────────────────"
echo " Resource group : $RG  ($LOCATION)"
echo " ACR            : $ACR"
echo " Web app        : https://$HOST"
echo " Postgres       : $PG / db=$PG_DB"
echo " Storage/share  : $STORAGE / $SHARE"
echo " Plan SKU       : $PLAN_SKU"
echo "──────────────────────────────────────────────────────────────"

# ── 1. Resource group ────────────────────────────────────────────────────────
echo "[1/9] Resource group..."
az group create -n "$RG" -l "$LOCATION" -o none

# ── 2. Container registry + cloud build ──────────────────────────────────────
echo "[2/9] Container registry..."
az acr create -g "$RG" -n "$ACR" --sku Basic --admin-enabled true -o none
echo "[2/9] Building image in the cloud (this can take 10-20 min: torch + SAM2)..."
az acr build --registry "$ACR" --image "$IMAGE" --file Dockerfile . -o none

ACR_SERVER=$(az acr show -n "$ACR" -g "$RG" --query loginServer -o tsv)
ACR_USER=$(az acr credential show -n "$ACR" -g "$RG" --query username -o tsv)
ACR_PASS=$(az acr credential show -n "$ACR" -g "$RG" --query 'passwords[0].value' -o tsv)

# ── 3. PostgreSQL flexible server ────────────────────────────────────────────
echo "[3/9] PostgreSQL flexible server..."
az postgres flexible-server create \
  -g "$RG" -n "$PG" -l "$LOCATION" \
  --tier "$PG_TIER" --sku-name "$PG_SKU" \
  --admin-user "$PG_ADMIN" --admin-password "$PG_PASSWORD" \
  --storage-size 32 --version 16 --public-access 0.0.0.0 --yes -o none
echo "[3/9] Database + allow Azure services..."
az postgres flexible-server db create -g "$RG" -s "$PG" -d "$PG_DB" -o none
az postgres flexible-server firewall-rule create \
  -g "$RG" -n "$PG" --rule-name allow-azure \
  --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 -o none

# ── 4. Storage account + file share (media) ─────────────────────────────────
echo "[4/9] Storage account + file share..."
az storage account create -g "$RG" -n "$STORAGE" -l "$LOCATION" \
  --sku Standard_LRS --kind StorageV2 -o none
STORAGE_KEY=$(az storage account keys list -g "$RG" -n "$STORAGE" --query '[0].value' -o tsv)
az storage share-rm create -g "$RG" --storage-account "$STORAGE" -n "$SHARE" --quota 50 -o none

# ── 5. App Service plan ──────────────────────────────────────────────────────
echo "[5/9] App Service plan ($PLAN_SKU, Linux)..."
az appservice plan create -g "$RG" -n "$PLAN" --is-linux --sku "$PLAN_SKU" -o none

# ── 6. Web App for Containers ────────────────────────────────────────────────
echo "[6/9] Web app for containers..."
az webapp create -g "$RG" -p "$PLAN" -n "$APP" \
  --deployment-container-image-name "$ACR_SERVER/$IMAGE" -o none
az webapp config container set -g "$RG" -n "$APP" \
  --container-image-name "$ACR_SERVER/$IMAGE" \
  --container-registry-url "https://$ACR_SERVER" \
  --container-registry-user "$ACR_USER" \
  --container-registry-password "$ACR_PASS" -o none

# ── 7. App settings (environment variables) ─────────────────────────────────
echo "[7/9] App settings..."
az webapp config appsettings set -g "$RG" -n "$APP" --settings \
  WEBSITES_PORT=8000 \
  WEBSITES_ENABLE_APP_SERVICE_STORAGE=false \
  WEBSITES_CONTAINER_START_TIME_LIMIT=1800 \
  SCM_DO_BUILD_DURING_DEPLOYMENT=false \
  DEBUG=False \
  SECRET_KEY="$SECRET_KEY" \
  ALLOWED_HOSTS="$HOST" \
  CSRF_TRUSTED_ORIGINS="https://$HOST" \
  SECURE_SSL_REDIRECT=True \
  USE_POSTGRES=True \
  POSTGRES_DB="$PG_DB" \
  POSTGRES_USER="$PG_ADMIN" \
  POSTGRES_PASSWORD="$PG_PASSWORD" \
  POSTGRES_HOST="$PG.postgres.database.azure.com" \
  POSTGRES_PORT=5432 \
  POSTGRES_SSLMODE=require \
  USE_S3=False \
  SERVE_MEDIA=True \
  SAM2_ENABLED=True \
  SAM2_REQUIRED="$SAM2_REQUIRED" \
  SAM2_DEVICE="$SAM2_DEVICE" \
  SAM2_POINTS_PER_SIDE="$SAM2_POINTS_PER_SIDE" \
  GUNICORN_WORKERS=1 \
  GUNICORN_THREADS=4 \
  GUNICORN_TIMEOUT=300 \
  DJANGO_SUPERUSER_USERNAME="$ADMIN_USER" \
  DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASSWORD" \
  DJANGO_SUPERUSER_EMAIL="$ADMIN_EMAIL" \
  SEED_DEMO=false -o none

# ── 8. Mount Azure Files for persistent media ───────────────────────────────
echo "[8/9] Mounting media share at /app/media..."
az webapp config storage-account add -g "$RG" -n "$APP" \
  --custom-id gvmedia --storage-type AzureFiles \
  --account-name "$STORAGE" --share-name "$SHARE" \
  --access-key "$STORAGE_KEY" --mount-path /app/media -o none

# ── 9. Restart + report ──────────────────────────────────────────────────────
echo "[9/9] Restarting web app..."
az webapp restart -g "$RG" -n "$APP" -o none

cat <<SUMMARY

============================================================
 DEPLOYED ✓   https://$HOST
============================================================
 Admin login (created automatically on first boot):
   username : $ADMIN_USER
   password : $ADMIN_PASSWORD
   role     : Admin

 Postgres admin password : $PG_PASSWORD
 Django SECRET_KEY        : (set in app settings)

 ⚠  SAVE THESE NOW — they are not stored anywhere else.

 First container start pulls a multi-GB image and can take
 several minutes. Watch logs with:
   az webapp log tail -g $RG -n $APP

 To change the admin password later, sign in and use the
 Users panel, or run (one-off):
   az webapp ssh -g $RG -n $APP
   # then inside: python manage.py changepassword $ADMIN_USER
============================================================
SUMMARY
