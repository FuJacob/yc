"""Thin wrapper around Sponge wallet SDK.

Hackathon scope: one method — send funds from the parent's wallet to a
preconfigured kid destination (another Sponge handle, USDC address, etc).
Lazy-imports paysponge so onboarding/grades still work even if the SDK
isn't installed or the API key is missing.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from config import PAYMENT_DEFAULT_CHAIN, SPONGE_API_KEY, SPONGE_API_URL

log = logging.getLogger(__name__)


class SpongePaymentError(RuntimeError):
    """User-safe Sponge execution failure."""


def _wallet():
    if not SPONGE_API_KEY:
        raise SpongePaymentError("Sponge is not configured: missing SPONGE_API_KEY.")
    try:
        from paysponge import SpongeWallet
    except ImportError as e:
        raise SpongePaymentError(
            "Sponge SDK is not installed. Run pip install -r requirements.txt."
        ) from e

    kwargs: dict[str, str] = {"api_key": SPONGE_API_KEY}
    if SPONGE_API_URL:
        kwargs["base_url"] = SPONGE_API_URL
    return SpongeWallet.connect(**kwargs)


def get_sponge_balances() -> dict[str, Any]:
    """Smoke-test helper: read the wallet's current balances."""
    return _to_jsonable(_wallet().get_balances())


def send_funds(
    *,
    to: str,
    amount_cents: int,
    currency: str = "USD",
    memo: str = "",
) -> dict[str, Any]:
    """Send funds from the parent's wallet to `to`.

    Returns a dict with at least `reference` and `status`. Raises
    SpongePaymentError with a user-safe message on failure.
    """
    if not to:
        raise SpongePaymentError("No payout destination is set for the kid.")
    if amount_cents <= 0:
        raise SpongePaymentError("Amount must be positive.")

    wallet = _wallet()
    amount_str = f"{Decimal(amount_cents) / Decimal(100):.2f}"

    transfer_currency = "USDC" if currency.upper() == "USD" else currency.upper()
    try:
        result = wallet.transfer(
            chain=PAYMENT_DEFAULT_CHAIN,
            to=to,
            amount=amount_str,
            currency=transfer_currency,
        )
    except Exception as e:
        raise SpongePaymentError(f"Sponge transfer failed: {e}") from e

    safe = _to_jsonable(result)
    return {
        "reference": _reference_from(safe) or to,
        "status": _status_from(safe) or "submitted",
        "raw": safe,
        "memo": memo,
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _reference_from(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    for key in ("reference", "id", "tx_hash", "txHash", "transactionHash",
                "hash", "payment_id", "paymentId", "session_id", "sessionId"):
        v = result.get(key)
        if v:
            return str(v)
    data = result.get("data")
    return _reference_from(data) if isinstance(data, dict) else None


def _status_from(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    for key in ("status", "state", "payment_status", "paymentStatus"):
        v = result.get(key)
        if v:
            return str(v).lower()
    data = result.get("data")
    return _status_from(data) if isinstance(data, dict) else None


def _to_jsonable(value: Any) -> Any:
    sensitive = ("secret", "token", "cvc", "pan", "card_number", "api_key")
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            ks = str(k)
            if any(s in ks.lower() for s in sensitive):
                out[ks] = "[redacted]"
            else:
                out[ks] = _to_jsonable(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump())
    if hasattr(value, "__dict__"):
        return _to_jsonable(vars(value))
    return str(value)
