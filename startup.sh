#!/bin/bash
set -e

echo "[startup] preparing data directories..."
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
