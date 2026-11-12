#!/bin/sh
set -eu

# Wait for the database before doing anything else. In Compose the database
# container may still be starting (or restarting), and its hostname does not
# resolve until it is running. Set DB_WAIT_SECONDS=0 to skip the wait.
if [ "${DB_WAIT_SECONDS:-60}" != "0" ]; then
  python - <<'PY'
import os
import sys
import time

import django

django.setup()

from django.db import connection

timeout = int(os.environ.get("DB_WAIT_SECONDS", "60"))
deadline = time.monotonic() + timeout
while True:
    try:
        connection.ensure_connection()
    except Exception as exc:  # noqa: BLE001 - report any connection failure
        if time.monotonic() >= deadline:
            print(f"Database still unavailable after {timeout}s: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Waiting for the database: {exc}", file=sys.stderr)
        connection.close()
        time.sleep(2)
    else:
        connection.close()
        print("Database is available.")
        break
PY
fi

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "Running database migrations..."
  python manage.py migrate --noinput
fi

exec "$@"
