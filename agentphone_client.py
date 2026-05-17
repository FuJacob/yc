import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone

import httpx

from config import (
    AGENT_PHONE_AGENT_ID,
    AGENT_PHONE_API_KEY,
    AGENT_PHONE_BASE_URL,
    AGENT_PHONE_NUMBER_ID,
    AGENT_PHONE_WEBHOOK_SECRET,
)

log = logging.getLogger(__name__)


def verify_signature(
    raw_body: bytes, timestamp_header: str, signature_header: str
) -> bool:
    """Verify AgentPhone webhook HMAC-SHA256 signature.

    Skipped (returns True) if no webhook secret is configured — useful in dev.
    """
    if not AGENT_PHONE_WEBHOOK_SECRET:
        return True

    if not timestamp_header or not signature_header:
        return False

    # Timestamp may arrive as either unix-epoch seconds or ISO-8601.
    ts_seconds: float | None = None
    try:
        ts_seconds = float(timestamp_header)
    except ValueError:
        try:
            dt = datetime.fromisoformat(timestamp_header.replace("Z", "+00:00"))
            ts_seconds = dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
        except ValueError:
            return False

    if abs(time.time() - ts_seconds) > 300:
        return False

    signed = f"{timestamp_header}.{raw_body.decode('utf-8', errors='replace')}"
    expected = "sha256=" + hmac.new(
        AGENT_PHONE_WEBHOOK_SECRET.encode(),
        signed.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def send_message(to_number: str, body: str) -> dict:
    """Send an SMS/iMessage via AgentPhone."""
    if not AGENT_PHONE_API_KEY or not AGENT_PHONE_AGENT_ID:
        raise RuntimeError(
            "AGENT_PHONE_API_KEY and AGENT_PHONE_AGENT_ID must be set"
        )

    payload: dict = {
        "agent_id": AGENT_PHONE_AGENT_ID,
        "to_number": to_number,
        "body": body,
    }
    if AGENT_PHONE_NUMBER_ID:
        payload["number_id"] = AGENT_PHONE_NUMBER_ID

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{AGENT_PHONE_BASE_URL}/v1/messages",
            headers={"Authorization": f"Bearer {AGENT_PHONE_API_KEY}"},
            json=payload,
        )
        if r.status_code >= 400:
            log.error("AgentPhone send_message failed: %s %s", r.status_code, r.text)
            r.raise_for_status()
        return r.json()
