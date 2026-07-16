#!/usr/bin/env bash
set -e

echo "[entrypoint] applying database migrations..."
for i in 1 2 3 4 5; do
  if python manage.py migrate --noinput; then
    break
  fi
  echo "[entrypoint] migrate failed (attempt $i) — DB may still be starting; retrying in 5s..."
  sleep 5
done

# Static is already collected at build time; refresh quietly if a volume hides it.
python manage.py collectstatic --noinput >/dev/null 2>&1 || true

# Optional: create an admin (gets the Admin role via the custom manager).
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  echo "[entrypoint] ensuring superuser '$DJANGO_SUPERUSER_USERNAME'..."
  python manage.py createsuperuser --noinput \
    --username "$DJANGO_SUPERUSER_USERNAME" \
    --email "${DJANGO_SUPERUSER_EMAIL:-admin@example.com}" \
    2>/dev/null && echo "[entrypoint] superuser created" \
    || echo "[entrypoint] superuser already exists — skipping"
fi

# Optional: load demo mandis/commodities/users.
if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "[entrypoint] seeding demo data..."
  python manage.py seed_demo || true
fi

echo "[entrypoint] starting gunicorn..."
exec gunicorn grainvision.wsgi:application -c docker/gunicorn.conf.py
