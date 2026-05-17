#!/usr/bin/env bash
# Restart: stop, then start. New tunnel URL; AgentPhone webhook is re-registered.
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/stop.sh
./scripts/start.sh
