#!/usr/bin/env bash
# Resend the verification text to a pending kid. Uses the same retry logic
# as the server. Useful when AgentPhone /v1/messages was down during the
# initial registration attempt.
#
# Usage:
#   scripts/resend-verification.sh              # finds the first pending kid
#   scripts/resend-verification.sh +17865551234 # specific kid phone

set -euo pipefail
cd "$(dirname "$0")/.."

KID_PHONE="${1:-}"

.venv/bin/python <<PY
import asyncio, os, sqlite3, sys
from dotenv import load_dotenv

load_dotenv(".env", override=True)
sys.path.insert(0, ".")
from agentphone_client import send_message

phone = "$KID_PHONE"

conn = sqlite3.connect("familyops.db")
conn.row_factory = sqlite3.Row

if phone:
    kid = conn.execute(
        "SELECT * FROM users WHERE phone = ? AND role = 'kid'", (phone,)
    ).fetchone()
else:
    kid = conn.execute(
        "SELECT * FROM users WHERE role = 'kid' AND onboarding_state = 'pending_verification' ORDER BY id DESC LIMIT 1"
    ).fetchone()

if not kid:
    print("no pending kid found")
    raise SystemExit(1)

parent = conn.execute(
    "SELECT * FROM users WHERE family_id = ? AND role = 'parent'",
    (kid["family_id"],),
).fetchone()

if kid["onboarding_state"] == "verified":
    print(f"{kid['name']} ({kid['phone']}) is already verified — skipping")
    raise SystemExit(0)

body = (
    f"Hi {kid['name']}, your parent {parent['name']} just registered you with "
    f"FamilyOps so they can help with school stuff like checking your grades. "
    f"Reply YES to confirm this is you."
)
print(f"sending to {kid['name']} at {kid['phone']}…")

async def main():
    r = await send_message(kid["phone"], body)
    print(f"sent: id={r.get('id')} status={r.get('status')}")

asyncio.run(main())
PY
