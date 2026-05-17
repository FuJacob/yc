#!/usr/bin/env bash
# Show what's running, the current tunnel URL, and the AgentPhone webhook config.
set -uo pipefail

cd "$(dirname "$0")/.."

SERVER_PID_FILE="/tmp/familyops-server.pid"
TUNNEL_PID_FILE="/tmp/familyops-tunnel.pid"
TUNNEL_LOG="/tmp/familyops-tunnel.log"

echo "=== local processes ==="
if [ -f "$SERVER_PID_FILE" ] && kill -0 "$(cat $SERVER_PID_FILE)" 2>/dev/null; then
  echo "server: running (pid $(cat $SERVER_PID_FILE))"
else
  echo "server: NOT running"
fi

if [ -f "$TUNNEL_PID_FILE" ] && kill -0 "$(cat $TUNNEL_PID_FILE)" 2>/dev/null; then
  echo "tunnel: running (pid $(cat $TUNNEL_PID_FILE))"
else
  echo "tunnel: NOT running"
fi

if [ -f "$TUNNEL_LOG" ]; then
  URL=$(grep "your url is:" "$TUNNEL_LOG" 2>/dev/null | head -1 | sed 's/.*your url is: //')
  if [ -n "$URL" ]; then
    echo "tunnel url: $URL"
  fi
fi

echo ""
echo "=== database ==="
if [ -f familyops.db ]; then
  if [ -x .venv/bin/python ]; then
    .venv/bin/python <<'PY'
import sqlite3
c = sqlite3.connect("familyops.db")
fam = c.execute("SELECT COUNT(*) FROM families").fetchone()[0]
usr = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
print(f"familyops.db: {fam} families, {usr} users")
for row in c.execute("SELECT id, name, role, phone, onboarding_state FROM users ORDER BY id"):
    print(f"  user {row[0]}: {row[1]} ({row[2]}) {row[3]} [{row[4]}]")
PY
  fi
else
  echo "familyops.db: missing"
fi

echo ""
echo "=== agentphone webhook ==="
if [ -x .venv/bin/python ]; then
  .venv/bin/python <<'PY'
import os, json
from dotenv import load_dotenv
import httpx
load_dotenv(".env", override=True)
key = os.environ.get("AGENT_PHONE_API_KEY", "")
agent_id = os.environ.get("AGENT_PHONE_AGENT_ID", "")
if not key or not agent_id:
    print("no AGENT_PHONE_API_KEY / AGENT_ID in .env")
    raise SystemExit
r = httpx.get(
    f"https://api.agentphone.ai/v1/agents/{agent_id}/webhook",
    headers={"Authorization": f"Bearer {key}"},
    timeout=10,
)
if r.status_code == 200:
    d = r.json()
    print(f"url:    {d.get('url')}")
    print(f"status: {d.get('status')}")
elif r.status_code == 404:
    print("no webhook configured for this agent")
else:
    print(f"error: {r.status_code} {r.text}")
PY
fi
