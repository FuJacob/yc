#!/usr/bin/env bash
# Start FastAPI + localtunnel + register the tunnel as the AgentPhone webhook.
# Idempotent: if already running, stops first.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
PORT=8000
SERVER_PID_FILE="/tmp/familyops-server.pid"
TUNNEL_PID_FILE="/tmp/familyops-tunnel.pid"
SERVER_LOG="/tmp/familyops-server.log"
TUNNEL_LOG="/tmp/familyops-tunnel.log"

# Stop any existing instances first
"$ROOT/scripts/stop.sh" >/dev/null 2>&1 || true

# Sanity check the venv
if [ ! -x ".venv/bin/uvicorn" ]; then
  echo "error: .venv not found. Run: /opt/homebrew/bin/python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# Start FastAPI
echo "starting FastAPI on :$PORT..."
nohup .venv/bin/uvicorn main:app --port "$PORT" > "$SERVER_LOG" 2>&1 &
echo $! > "$SERVER_PID_FILE"

# Wait until /health responds
for i in {1..20}; do
  if curl -sf "localhost:$PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
  if [ "$i" = 20 ]; then
    echo "error: server did not come up. See $SERVER_LOG"
    exit 1
  fi
done
echo "server up (pid $(cat $SERVER_PID_FILE))"

# Start localtunnel
echo "starting localtunnel..."
nohup lt --port "$PORT" > "$TUNNEL_LOG" 2>&1 &
echo $! > "$TUNNEL_PID_FILE"

# Wait for URL to appear in log
TUNNEL_URL=""
for i in {1..20}; do
  if grep -q "your url is:" "$TUNNEL_LOG" 2>/dev/null; then
    TUNNEL_URL=$(grep "your url is:" "$TUNNEL_LOG" | head -1 | sed 's/.*your url is: //')
    break
  fi
  sleep 0.5
done

if [ -z "$TUNNEL_URL" ]; then
  echo "error: tunnel did not produce a URL. See $TUNNEL_LOG"
  cat "$TUNNEL_LOG"
  exit 1
fi
echo "tunnel up: $TUNNEL_URL"

# Register the webhook with AgentPhone
WEBHOOK_URL="$TUNNEL_URL/webhook"
echo "registering webhook with AgentPhone..."
.venv/bin/python <<PY
import os, json, sys
from dotenv import load_dotenv
import httpx
load_dotenv("$ROOT/.env", override=True)
key = os.environ["AGENT_PHONE_API_KEY"]
agent_id = os.environ["AGENT_PHONE_AGENT_ID"]
r = httpx.post(
    f"https://api.agentphone.ai/v1/agents/{agent_id}/webhook",
    headers={"Authorization": f"Bearer {key}"},
    json={"url": "$WEBHOOK_URL"},
    timeout=15,
)
if r.status_code >= 400:
    print(f"webhook register failed: {r.status_code} {r.text}")
    sys.exit(1)
data = r.json()
print(f"webhook registered: id={data['id']} status={data['status']}")
PY

echo ""
echo "ready."
echo "  server pid:  $(cat $SERVER_PID_FILE)"
echo "  tunnel pid:  $(cat $TUNNEL_PID_FILE)"
echo "  tunnel url:  $TUNNEL_URL"
echo "  webhook:     $WEBHOOK_URL"
echo "  server log:  $SERVER_LOG (use scripts/logs.sh)"
