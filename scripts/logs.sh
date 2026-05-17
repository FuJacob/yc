#!/usr/bin/env bash
# Tail the FastAPI server log. Pass -t to also tail the tunnel log.
set -euo pipefail

SERVER_LOG="/tmp/familyops-server.log"
TUNNEL_LOG="/tmp/familyops-tunnel.log"

if [ "${1:-}" = "-t" ] || [ "${1:-}" = "--tunnel" ]; then
  exec tail -F "$SERVER_LOG" "$TUNNEL_LOG"
fi

if [ ! -f "$SERVER_LOG" ]; then
  echo "no server log at $SERVER_LOG — is the server running? (scripts/start.sh)"
  exit 1
fi
exec tail -F "$SERVER_LOG"
