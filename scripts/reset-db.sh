#!/usr/bin/env bash
# Wipe familyops.db so the next request starts with empty families/users tables.
# Does NOT restart the server — the DB connection is opened per-request, so a
# fresh DB will be recreated automatically on next inbound webhook.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f familyops.db ]; then
  rm -f familyops.db familyops.db-journal
  echo "wiped familyops.db"
else
  echo "no familyops.db to wipe"
fi

# Re-run init via the live server's startup path: hit /health which doesn't
# trigger init, so instead just re-create the schema directly.
if [ -x .venv/bin/python ]; then
  .venv/bin/python -c "from db import init_db; init_db(); print('schema recreated')"
fi
