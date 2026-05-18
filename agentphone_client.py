import asyncio
import hashlib
import hmac
import logging

import httpx

from config import (
    AGENT_PHONE_AGENT_ID,
    AGENT_PHONE_API_KEY,
    AGENT_PHONE_BASE_URL,
    AGENT_PHONE_NUMBER_ID,
    AGENT_PHONE_WEBHOOK_SECRET,
)

log = logging.getLogger(__name__)

SEND_BACKOFF_SECONDS = (0.3, 1.0, 3.0, 7.0)

# Shared persistent client — reuses TCP+TLS connections across calls.
_http: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            timeout=20,
            headers={"Authorization": f"Bearer {AGENT_PHONE_API_KEY}"},
        )
    return _http


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    if not AGENT_PHONE_WEBHOOK_SECRET:
        return True
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        AGENT_PHONE_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def send_message(to_number: str, body: str) -> dict:
    """Send an SMS/iMessage via AgentPhone, retrying on transient 5xx / timeouts."""
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

    client = _get_client()
    last_error: Exception | None = None
    for attempt, backoff in enumerate([0.0, *SEND_BACKOFF_SECONDS]):
        if backoff:
            log.warning(
                "send_message retry %d in %.1fs (last error: %s)",
                attempt,
                backoff,
                last_error,
            )
            await asyncio.sleep(backoff)
        try:
            r = await client.post(
                f"{AGENT_PHONE_BASE_URL}/v1/messages",
                json=payload,
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_error = e
            continue

        if r.status_code < 400:
            return r.json()

        if 400 <= r.status_code < 500:
            log.error("send_message client error %d: %s", r.status_code, r.text[:300])
            r.raise_for_status()

        last_error = httpx.HTTPStatusError(
            f"{r.status_code} server error", request=r.request, response=r
        )
        log.warning("send_message server error %d (will retry)", r.status_code)

    log.error("send_message exhausted retries: %s", last_error)
    raise last_error if last_error else RuntimeError("send_message failed without an error")
