#!/usr/bin/env bash
set -o errexit
python manage.py migrate --noinput
if [ "${APP_ENV:-local}" = "production" ] || [ "${APP_ENV:-local}" = "development" ]; then
  python manage.py bootstrap_owner
else
  python manage.py bootstrap_preview
fi
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-10000}" --workers 1 --threads 4 --timeout 60 --access-logfile - --error-logfile -
