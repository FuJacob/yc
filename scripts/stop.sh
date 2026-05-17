#!/usr/bin/env bash
# Stop FastAPI server + localtunnel.
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

# Belt-and-suspenders: kill any uvicorn on :8000 and any localtunnel client
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "localtunnel" 2>/dev/null || true

echo "done."
