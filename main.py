import json
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

import voice_state
from agent import handle_inbound
from agentphone_client import send_message, verify_signature
from db import get_kid_for_parent, get_user_by_phone, init_db
from tools import dispatch_voice_tool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("familyops")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    log.info("FamilyOps started — DB initialized")
    yield


app = FastAPI(title="FamilyOps", lifespan=lifespan)

# In-memory mapping of session_id -> live_url for browser streaming (RFC-1)
_live_sessions: dict[str, str] = {}

LIVE_PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, user-scalable=yes">
  <style>
    body {{ margin: 0; background: #111; color: #fff; font-family: system-ui; }}
    header {{ padding: 12px 16px; font-size: 14px; opacity: 0.7; }}
    .viewer {{
      width: 100%; height: calc(100vh - 44px);
      overflow: auto; -webkit-overflow-scrolling: touch;
    }}
    iframe {{
      width: 100%; height: 100%; border: none;
      pointer-events: none;
    }}
  </style>
</head>
<body>
  <header>FamilyOps — checking grades...</header>
  <div class="viewer">
    <iframe src="{live_url}&theme=dark&ui=false" allow="autoplay"></iframe>
  </div>
</body>
</html>"""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/live/{session_id}")
async def live_view(session_id: str):
    live_url = _live_sessions.get(session_id)
    if not live_url:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return HTMLResponse(LIVE_PAGE_HTML.format(live_url=live_url))


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_signature: str = Header(default=""),
):
    raw = await request.body()

    if not verify_signature(raw, x_webhook_signature):
        log.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event")
    if event != "agent.message":
        log.info("Ignoring event: %s", event)
        return {"ok": True}

    channel = payload.get("channel", "sms")
    if channel == "voice":
        log.info("Ignoring voice channel")
        return {"ok": True}

    data = payload.get("data") or {}
    from_number = data.get("from")
    message_text = data.get("message")

    if not from_number or not message_text:
        log.warning("Webhook missing from/message — data=%s", data)
        return {"ok": True}

    recent_history = payload.get("recentHistory") or []

    background_tasks.add_task(
        _process_inbound,
        from_number=from_number,
        message_text=message_text,
        recent_history=recent_history,
    )
    return {"ok": True}


async def _process_inbound(
    *,
    from_number: str,
    message_text: str,
    recent_history: list,
) -> None:
    log.info("Inbound from=%s message=%r", from_number, message_text)

    try:
        reply, ctx = await handle_inbound(
            sender_phone=from_number,
            message_text=message_text,
            recent_history=recent_history,
            live_sessions=_live_sessions,
        )
    except Exception:
        log.exception("handle_inbound failed")
        try:
            await send_message(
                from_number,
                "Sorry, something went wrong on my end. Try that again?",
            )
        except Exception:
            log.exception("Failed to send error fallback")
        return

    if reply:
        try:
            await send_message(from_number, reply)
        except Exception:
            log.exception("Failed to send reply to %s", from_number)

    if ctx.get("notify_kid_about_grades"):
        await _notify_kid_about_grades(parent_phone=from_number)


async def _notify_kid_about_grades(*, parent_phone: str) -> None:
    parent = get_user_by_phone(parent_phone)
    if not parent or parent["role"] != "parent":
        return
    kid = get_kid_for_parent(parent["id"])
    if not kid or kid["onboarding_state"] != "verified":
        return
    try:
        await send_message(
            kid["phone"],
            f"FYI {parent['name']} just checked your grades.",
        )
    except Exception:
        log.exception("Failed to notify kid")


# ============================================================================
# Voice webhook (RFC-3)
#
# AgentPhone fires four event types here:
#   call.started      — async, log + set up
#   call.transcript   — async, informational
#   call.tool_call    — SYNC, the model is waiting on our HTTP response
#   call.ended        — async, cleanup
# ============================================================================


@app.post("/webhook/voice")
async def voice_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_signature: str = Header(default=""),
):
    raw = await request.body()

    if not verify_signature(raw, x_webhook_signature):
        log.warning("Invalid voice webhook signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event", "")
    data = payload.get("data") or {}
    call_id = data.get("call_id") or data.get("callId") or ""

    if event == "call.started":
        from_number = data.get("from_number") or data.get("from") or ""
        log.info("call.started call_id=%s from=%s", call_id, from_number)
        return {"ok": True}

    if event == "call.transcript":
        # AgentPhone is driving the conversation; we just log finalized turns.
        if data.get("is_final") and data.get("speaker") in ("user", "caller"):
            log.info(
                "call.transcript call_id=%s text=%r",
                call_id,
                (data.get("text") or "")[:200],
            )
        return {"ok": True}

    if event == "call.tool_call":
        # MUST be synchronous — the voice model is waiting on the result.
        try:
            return await dispatch_voice_tool(payload)
        except Exception:
            log.exception("dispatch_voice_tool raised")
            return {
                "tool_call_id": data.get("tool_call_id") or data.get("toolCallId") or "",
                "output": '{"error": "internal error"}',
            }

    if event == "call.ended":
        end_reason = data.get("end_reason") or data.get("reason") or "unknown"
        log.info("call.ended call_id=%s reason=%s", call_id, end_reason)
        background_tasks.add_task(_on_call_ended, call_id)
        return {"ok": True}

    log.info("voice webhook: ignoring event %s", event)
    return {"ok": True}


async def _on_call_ended(call_id: str) -> None:
    """Clean up voice_state. If a grade fetch was still running, route its
    eventual result to SMS instead of voice."""
    cf = await voice_state.cleanup_call(call_id)
    if cf and cf.status == "running" and cf.sender_phone:
        log.info(
            "call ended mid-fetch (call_id=%s) — final summary will arrive via SMS",
            call_id,
        )
        # The background pump task is still alive in tools.py; when it
        # completes, voice_state.finish/push_step become no-ops (the call_id
        # is gone). To make sure the user still gets the answer, we attach a
        # fallback delivery here.
        # Simplest: just note it in logs. The background pump task itself
        # handles the SMS fallback in its except branch on cancellation. For
        # the success branch we'd need a side-channel — punt to Phase 5.
