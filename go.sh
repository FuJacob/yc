#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
bash scripts/reset-db.sh
bash scripts/stop.sh 2>/dev/null || true
bash scripts/start.sh
