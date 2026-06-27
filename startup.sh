#!/bin/bash
# Startup for deploying GrainVision as CODE on Azure App Service (no Docker).
# App Service runs this via the "Startup Command": bash startup.sh
set -e

echo "[startup] preparing data directories..."
# Make sure the folders for the database and uploads exist (e.g. /home/data),
# otherwise SQLite can't create its file and uploads have nowhere to go.
mkdir -p "$(dirname "${SQLITE_PATH:-db.sqlite3}")" "${MEDIA_ROOT:-media}" 2>/dev/null || true

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
