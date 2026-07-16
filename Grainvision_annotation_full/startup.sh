#!/bin/bash
# Startup for deploying GrainVision as CODE on Azure App Service (no Docker).
# Set as the "Startup Command":  bash startup.sh
set -e

echo "[startup] preparing data directories..."
mkdir -p "$(dirname "${SQLITE_PATH:-db.sqlite3}")" "${MEDIA_ROOT:-media}" 2>/dev/null || true

# Optional: SAM2-on-CPU. Only downloads the model when SAM2 is turned on.
if [ "${SAM2_ENABLED}" = "True" ] && [ "${SAM2_DEVICE:-cpu}" = "cpu" ]; then
  MODELS="$(dirname "${SAM2_CHECKPOINT:-/home/data/models/sam2.1_hiera_small.pt}")"
  mkdir -p "$MODELS" 2>/dev/null || true
  if [ ! -f "${SAM2_CHECKPOINT:-/home/data/models/sam2.1_hiera_small.pt}" ]; then
    echo "[startup] downloading SAM2 checkpoint (one-time)..."
    curl -L -o "${SAM2_CHECKPOINT:-/home/data/models/sam2.1_hiera_small.pt}" \
      https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt || true
  fi
fi

echo "[startup] migrating database..."
python manage.py migrate --noinput

echo "[startup] collecting static files..."
python manage.py collectstatic --noinput || true

echo "[startup] ensuring admin user..."
python manage.py ensure_admin || true

echo "[startup] starting gunicorn..."
exec gunicorn grainvision.wsgi:application \
  --bind=0.0.0.0:${PORT:-8000} \
  --workers=${GUNICORN_WORKERS:-2} \
  --timeout=${GUNICORN_TIMEOUT:-120}
