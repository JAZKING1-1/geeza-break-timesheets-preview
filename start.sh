#!/usr/bin/env bash
set -o errexit
python manage.py migrate --noinput
python manage.py bootstrap_preview
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-10000}" --workers 1 --threads 4 --timeout 60 --access-logfile - --error-logfile -
