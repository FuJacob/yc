#!/usr/bin/env bash
# Start FastAPI + ngrok + register the tunnel as the AgentPhone webhook.
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

# Sanity check ngrok auth
if ! ngrok config check >/dev/null 2>&1; then
  echo "error: ngrok not configured. Run: ngrok config add-authtoken <YOUR_TOKEN>"
  echo "  Get a token at: https://dashboard.ngrok.com/get-started/your-authtoken"
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

# Start ngrok
echo "starting ngrok..."
nohup ngrok http "$PORT" --log=stdout > "$TUNNEL_LOG" 2>&1 &
echo $! > "$TUNNEL_PID_FILE"

# Wait for the tunnel to register, then query the local API for the public URL
TUNNEL_URL=""
for i in {1..30}; do
  TUNNEL_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
    | .venv/bin/python -c "import json,sys; d=json.load(sys.stdin); t=d.get('tunnels',[]); print(t[0]['public_url']) if t else None" \
    2>/dev/null || true)
  if [ -n "$TUNNEL_URL" ]; then
    break
  fi
  sleep 0.5
done

if [ -z "$TUNNEL_URL" ]; then
  echo "error: ngrok did not produce a URL. See $TUNNEL_LOG"
  tail -20 "$TUNNEL_LOG"
  exit 1
fi
echo "tunnel up: $TUNNEL_URL"

# Register the webhook with AgentPhone (non-fatal on AP outage; the URL is often
# already correct from a prior run since ngrok-free reuses subdomains short-term)
WEBHOOK_URL="$TUNNEL_URL/webhook"
echo "registering webhook with AgentPhone..."
.venv/bin/python <<PY || echo "  (could not register — see scripts/status.sh for current webhook config)"
import os, sys
from dotenv import load_dotenv
import httpx
load_dotenv("$ROOT/.env", override=True)
key = os.environ["AGENT_PHONE_API_KEY"]
agent_id = os.environ["AGENT_PHONE_AGENT_ID"]
try:
    r = httpx.post(
        f"https://api.agentphone.ai/v1/agents/{agent_id}/webhook",
        headers={"Authorization": f"Bearer {key}"},
        json={"url": "$WEBHOOK_URL"},
        timeout=10,
    )
except httpx.HTTPError as e:
    print(f"  AgentPhone unreachable: {type(e).__name__}")
    sys.exit(2)
if r.status_code >= 400:
    print(f"  webhook register failed: {r.status_code} {r.text[:200]}")
    sys.exit(1)
data = r.json()
print(f"  webhook registered: id={data['id']} status={data['status']}")
PY

# Persist the URL for downstream code (PUBLIC_URL used by RFC-1 live page)
echo "$TUNNEL_URL" > /tmp/familyops-tunnel-url

echo ""
echo "ready."
echo "  server pid:  $(cat $SERVER_PID_FILE)"
echo "  tunnel pid:  $(cat $TUNNEL_PID_FILE)"
echo "  tunnel url:  $TUNNEL_URL"
echo "  webhook:     $WEBHOOK_URL"
echo "  server log:  $SERVER_LOG"
echo "  tunnel log:  $TUNNEL_LOG"
echo ""

exec "$ROOT/scripts/logs.sh"
