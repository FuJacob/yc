#!/usr/bin/env bash
# Wipe familyops.db AND Supermemory so the next request starts completely fresh.
# Does NOT restart the server — the DB connection is opened per-request, so a
# fresh DB will be recreated automatically on next inbound webhook.
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Purge Supermemory containers (must happen BEFORE DB wipe so we could
#    theoretically read family_ids, but we just brute-force 1..100).
if [ -x .venv/bin/python ]; then
  echo "purging supermemory..."
  .venv/bin/python -c "
import asyncio
from memory import purge_all_containers
deleted = asyncio.run(purge_all_containers())
print(f'supermemory: purged {deleted} documents')
"
else
  echo "warning: .venv/bin/python not found — skipping supermemory purge"
fi

# 2. Wipe SQLite
if [ -f familyops.db ]; then
  rm -f familyops.db familyops.db-journal
  echo "wiped familyops.db"
else
  echo "no familyops.db to wipe"
fi

# 3. Re-create empty schema
if [ -x .venv/bin/python ]; then
  .venv/bin/python -c "from db import init_db; init_db(); print('schema recreated')"
fi
