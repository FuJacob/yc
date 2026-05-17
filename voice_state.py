"""In-memory state for active voice calls.

The voice agent polls `check_d2l_grades` repeatedly while a fetch runs. Each
poll returns the next chunk to read aloud. This module holds that chunk queue
plus the final summary, keyed by AgentPhone `call_id`.

Single FastAPI process. Cleared on restart — fine for a hackathon. The lock
guards against the (unlikely) case where the poll and the background pump
race on the same key.
"""

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class CallFetch:
    handle: str
    student_name: str
    sender_phone: str
    status: str = "running"          # "running" | "done" | "error"
    step_queue: list[str] = field(default_factory=list)
    final_summary: Optional[str] = None


_state: dict[str, CallFetch] = {}
_lock = asyncio.Lock()


def new_handle() -> str:
    return f"sess_{secrets.token_hex(6)}"


async def start(call_id: str, handle: str, student_name: str, sender_phone: str) -> None:
    async with _lock:
        _state[call_id] = CallFetch(
            handle=handle,
            student_name=student_name,
            sender_phone=sender_phone,
        )
    log.info("voice_state.start call_id=%s handle=%s student=%s", call_id, handle, student_name)


async def push_step(call_id: str, step: str) -> None:
    async with _lock:
        cf = _state.get(call_id)
        if cf is None:
            log.debug("push_step on cleared call %s — dropping", call_id)
            return
        cf.step_queue.append(step)


async def finish(call_id: str, summary: str, *, status: str = "done") -> None:
    async with _lock:
        cf = _state.get(call_id)
        if cf is None:
            log.debug("finish on cleared call %s — dropping", call_id)
            return
        cf.status = status
        cf.final_summary = summary
    log.info("voice_state.finish call_id=%s status=%s", call_id, status)


async def next_chunk(call_id: str) -> dict:
    """Return the next chunk for the model to read aloud.

    Shape:
      {"status": "running", "step": "looking at the course list", "handle": "..."}
      {"status": "running", "step": null, "handle": "..."}      # no new step yet
      {"status": "done",    "summary": "...", "handle": "..."}
      {"status": "error",   "message": "..."}
    """
    async with _lock:
        cf = _state.get(call_id)
        if cf is None:
            return {"status": "error", "message": "no active fetch"}

        if cf.step_queue:
            return {
                "status": "running",
                "step": cf.step_queue.pop(0),
                "handle": cf.handle,
            }

        if cf.status == "done":
            return {
                "status": "done",
                "summary": cf.final_summary or "",
                "handle": cf.handle,
            }

        if cf.status == "error":
            return {
                "status": "error",
                "message": cf.final_summary or "fetch failed",
                "handle": cf.handle,
            }

        # running, queue empty
        return {"status": "running", "step": None, "handle": cf.handle}


async def cleanup_call(call_id: str) -> Optional[CallFetch]:
    """Drop a call's state (called on call.ended). Returns the dropped record."""
    async with _lock:
        return _state.pop(call_id, None)


async def get_sender_phone(call_id: str) -> Optional[str]:
    async with _lock:
        cf = _state.get(call_id)
        return cf.sender_phone if cf else None
