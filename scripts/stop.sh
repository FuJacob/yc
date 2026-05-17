#!/usr/bin/env bash
# Stop FastAPI server + ngrok tunnel.
set -uo pipefail

SERVER_PID_FILE="/tmp/familyops-server.pid"
TUNNEL_PID_FILE="/tmp/familyops-tunnel.pid"

_kill_pidfile() {
  local label="$1"
  local pidfile="$2"
  if [ -f "$pidfile" ]; then
    local pid
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 0.3
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
      fi
      echo "stopped $label (pid $pid)"
    fi
    rm -f "$pidfile"
  fi
}

_kill_pidfile "server" "$SERVER_PID_FILE"
_kill_pidfile "tunnel" "$TUNNEL_PID_FILE"

# Belt-and-suspenders: kill any uvicorn on :8000 and any ngrok
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "ngrok http 8000" 2>/dev/null || true

rm -f /tmp/familyops-tunnel-url

echo "done."
