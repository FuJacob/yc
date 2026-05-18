import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from agent import handle_inbound
from agentphone_client import send_message, verify_signature
from db import init_db

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

# Per-phone message queue: ensures one message is fully processed before the
# next starts, preventing interleaved replies during long-running tools like
# Browser Use.  Each phone gets its own asyncio.Queue and a single worker task.
_phone_queues: dict[str, asyncio.Queue] = {}
_phone_workers: dict[str, asyncio.Task] = {}


def _enqueue(phone: str, coro_args: dict) -> None:
    if phone not in _phone_queues:
        _phone_queues[phone] = asyncio.Queue()
        _phone_workers[phone] = asyncio.create_task(_phone_worker(phone))
    _phone_queues[phone].put_nowait(coro_args)


async def _phone_worker(phone: str) -> None:
    q = _phone_queues[phone]
    while True:
        args = await q.get()
        try:
            await _process_inbound(**args)
        except Exception:
            log.exception("Queued _process_inbound failed for %s", phone)
        finally:
            q.task_done()

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
    <iframe src="{iframe_src}" allow="autoplay"></iframe>
  </div>
</body>
</html>"""


def _build_iframe_src(live_url: str) -> str:
    """Append theme/ui params to the live_url, picking `?` or `&` correctly."""
    sep = "&" if "?" in live_url else "?"
    return f"{live_url}{sep}theme=dark&ui=false"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/live/{session_id}")
async def live_view(session_id: str):
    live_url = _live_sessions.get(session_id)
    if not live_url:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return HTMLResponse(
        LIVE_PAGE_HTML.format(iframe_src=_build_iframe_src(live_url))
    )


@app.post("/webhook")
async def webhook(
    request: Request,
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

    data = payload.get("data") or {}
    from_number = data.get("from")
    message_text = data.get("message")

    if not from_number or not message_text:
        log.warning("Webhook missing from/message — data=%s", data)
        return {"ok": True}

    recent_history = payload.get("recentHistory") or []

    _enqueue(from_number, dict(
        from_number=from_number,
        message_text=message_text,
        recent_history=recent_history,
    ))
    return {"ok": True}


async def _process_inbound(
    *,
    from_number: str,
    message_text: str,
    recent_history: list,
) -> None:
    log.info("Inbound from=%s message=%r", from_number, message_text)
    if recent_history:
        log.info(
            "recentHistory[0] keys=%s entry=%s",
            list(recent_history[0].keys()),
            json.dumps(recent_history[0], default=str)[:500],
        )

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

