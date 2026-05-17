# RFC-5: Migrate from iMessage (AgentPhone) to Standard SMS

**Status:** Draft
**Date:** 2026-05-17
**Depends on:** RFC.md (core MVP)

---

## Problem

AgentPhone's iMessage delivery is unreliable — messages aren't reaching the parent's phone
despite the agent processing correctly. Outbound SMS requires 10DLC registration which we
haven't completed. We're blocked on a third-party compliance issue with no fast workaround.

## Solution

Drop AgentPhone entirely. Switch to a standard SMS provider (Twilio or Vonage) that either:
- Already has 10DLC pre-approved for our use case, or
- Supports toll-free numbers that don't require 10DLC for low-volume A2P messaging

This gives us reliable two-way SMS immediately without waiting on AgentPhone's registration
process.

---

## Options

### Option A: Twilio (Recommended)

- **Toll-free number** — no 10DLC needed for low-volume conversational SMS. Instant provisioning.
- Webhook-based inbound (same pattern we already have).
- Well-documented, battle-tested Python SDK (`twilio`).
- Cost: ~$2/mo for number + $0.0079/SMS segment.
- Can upgrade to 10DLC later if we scale.

### Option B: Vonage (Backup)

- Similar toll-free approach.
- Less mature Python SDK.
- Slightly cheaper but more complex webhook verification.

### Option C: Fix AgentPhone 10DLC

- Complete 10DLC registration on AgentPhone dashboard.
- **Risk:** Registration can take 1-7 days for approval. Not viable for today's demo.

**Decision: Option A (Twilio with toll-free number).**

---

## Architecture Change

```
BEFORE:
  Parent texts AgentPhone iMessage number
  → AgentPhone webhook POST /webhook
  → Our server processes, replies via AgentPhone API
  → AgentPhone delivers via iMessage (BROKEN)

AFTER:
  Parent texts Twilio toll-free number
  → Twilio webhook POST /webhook/sms
  → Our server processes, replies via Twilio API
  → Twilio delivers via SMS (reliable, instant)
```

The internal orchestrator, tools, browser agent — all unchanged. Only the messaging
transport layer swaps out.

---

## Detailed Design

### 1. Twilio Setup

```bash
pip install twilio
```

Provision a toll-free number via Twilio console or CLI:
```bash
twilio phone-numbers:buy:tollfree --country-code US
```

Configure the number's webhook URL to `https://{NGROK_URL}/webhook/sms` (HTTP POST).

### 2. New Env Vars

```
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...   # the toll-free number
```

### 3. Replace `agentphone_client.py`

```python
import logging
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER

log = logging.getLogger(__name__)
_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
_validator = RequestValidator(TWILIO_AUTH_TOKEN)


async def send_message(to_number: str, body: str) -> dict:
    """Send SMS via Twilio."""
    message = _client.messages.create(
        body=body,
        from_=TWILIO_PHONE_NUMBER,
        to=to_number,
    )
    log.info("Sent SMS to=%s sid=%s", to_number, message.sid)
    return {"sid": message.sid, "status": message.status}


def verify_signature(url: str, params: dict, signature: str) -> bool:
    """Verify Twilio webhook signature."""
    if not TWILIO_AUTH_TOKEN:
        return True  # skip in dev
    return _validator.validate(url, params, signature)
```

### 4. Update Webhook Handler (`main.py`)

Twilio sends form-encoded POST (not JSON). The inbound payload fields:
- `From` — sender phone (E.164)
- `Body` — message text
- `To` — our Twilio number

```python
from fastapi import Form

@app.post("/webhook/sms")
async def webhook_sms(
    request: Request,
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
):
    # Optional: verify Twilio signature
    # signature = request.headers.get("X-Twilio-Signature", "")
    # if not verify_signature(str(request.url), dict(await request.form()), signature):
    #     raise HTTPException(401)

    background_tasks.add_task(
        _process_inbound,
        from_number=From,
        message_text=Body,
        recent_history=[],  # Twilio doesn't provide history — we manage our own or skip
    )
    # Twilio expects TwiML response (empty is fine for async processing)
    return Response(content="<Response/>", media_type="application/xml")
```

### 5. Config Changes

```python
# In config.py
TWILIO_ACCOUNT_SID = _env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = _env("TWILIO_PHONE_NUMBER")
```

### 6. Conversation History

AgentPhone provided `recentHistory` in the webhook payload for free. Twilio does not.

Options:
- **(A)** Skip history — orchestrator works without it (less context but functional).
- **(B)** Store last N messages per phone in SQLite and pass them to the orchestrator.
- **(C)** Fetch from Twilio Messages API on each inbound (adds latency).

**For today: Option A.** The orchestrator's system prompt + DB context is sufficient for
the demo flows (onboarding, verification, grade check). History is nice-to-have, not blocking.

---

## Files Changed

| File | Change |
|---|---|
| `agentphone_client.py` | Rewrite → `sms_client.py` (Twilio SDK) |
| `main.py` | New `/webhook/sms` endpoint (form-encoded), keep old `/webhook` for backwards compat |
| `config.py` | Add `TWILIO_*` vars, keep `AGENT_PHONE_*` (dead but harmless) |
| `requirements.txt` | Add `twilio`, can remove `httpx` if only used for AgentPhone |
| `.env.example` | Add Twilio vars |

---

## Migration Steps

### Phase 1: Twilio provisioning (~5 min)
1. Sign up / log into Twilio console
2. Buy a toll-free number
3. Set webhook URL to `https://{NGROK_URL}/webhook/sms`
4. Add `TWILIO_*` vars to `.env`

### Phase 2: Code swap (~10 min)
5. Create `sms_client.py` with Twilio `send_message` + `verify_signature`
6. Add `/webhook/sms` route to `main.py`
7. Update all imports from `agentphone_client` → `sms_client`
8. Update `requirements.txt`

### Phase 3: Test (~5 min)
9. Text the Twilio number from parent phone
10. Verify onboarding flow works end-to-end
11. Verify grade check triggers and replies arrive

---

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Twilio toll-free verification required for high volume | Low | We're low volume for demo. Verification takes 1-2 days if needed later. |
| No conversation history from Twilio | Medium | Orchestrator works without it. Add SQLite history store post-demo if needed. |
| SMS character limit (160 chars/segment) | Low | Grade summaries may split into multiple segments. Twilio handles this transparently. |
| Twilio trial account limitations | Low | Trial only sends to verified numbers. Upgrade to paid ($20 min) for unrestricted. |

---

## What We're NOT Doing

- Keeping AgentPhone as fallback (clean break)
- Building conversation history storage (post-demo)
- iMessage-specific features (read receipts, rich links)
- Voice via Twilio (separate concern, RFC-3 handles voice)

---

## Rollback

If Twilio has issues, the old AgentPhone code stays in git history. Revert the commit and
re-register the AgentPhone webhook.
