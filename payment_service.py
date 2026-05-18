"""Payment request state machine — kid asks, parent approves, money moves.

Money flow: parent's Sponge wallet → kid's preconfigured payout destination.
The "service" the kid names is just descriptive text on the approval prompt;
funds always land at the kid's stored destination.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any

from agentphone_client import send_message
from db import (
    append_event,
    create_payment_request as db_create_payment_request,
    get_parent_for_kid,
    get_payment_request_by_code,
    get_payment_request_by_id,
    get_user_by_id,
    get_user_by_phone,
    is_expired,
    list_pending_requests_for_family,
    set_payout_destination,
    transition_status,
)
from config import KID_DEFAULT_PAYOUT_DESTINATION, PAYMENT_DEFAULT_CHAIN
from sponge_client import SpongePaymentError, send_funds, validate_payout_destination

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool entry points
# ---------------------------------------------------------------------------


async def create_payment_request(
    *,
    sender_phone: str,
    service_name: str,
    amount_cents: int,
    reason: str = "",
    currency: str = "USD",
) -> str:
    kid = _require_verified(sender_phone, role="kid")
    if isinstance(kid, str):
        return kid

    parent = get_parent_for_kid(kid["id"])
    if not parent:
        return "ERROR: no parent is registered for this kid."

    service_name = (service_name or "").strip()
    reason = (reason or "").strip()
    currency = (currency or "USD").strip().upper()
    if not service_name:
        return "ERROR: service_name is required."
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        return "ERROR: amount_cents must be an integer (e.g. 200 for $2.00)."
    if amount_cents <= 0:
        return "ERROR: amount_cents must be positive."

    req = db_create_payment_request(
        family_id=kid["family_id"],
        kid_user_id=kid["id"],
        service_name=service_name,
        description=reason,
        amount_cents=amount_cents,
        currency=currency,
    )

    amount_str = _format_money(amount_cents, currency)
    reason_clause = f": '{reason}'" if reason else ""
    parent_msg = (
        f"{kid['name']} wants {amount_str} for {service_name}{reason_clause}. "
        f"Want to go ahead? Just say yes or no (ref: {req['request_code']})."
    )
    try:
        await send_message(parent["phone"], parent_msg)
        append_event(
            payment_request_id=req["id"],
            actor_user_id=None,
            event_type="parent_notified",
            message="Parent notified for payment approval.",
        )
    except Exception as e:
        log.exception("Failed to notify parent for payment request %s", req["id"])
        return (
            f"Created request {req['request_code']} but couldn't text "
            f"{parent['name']} for approval: {e}"
        )

    return (
        f"Asked {parent['name']} to approve {amount_str} for {service_name}. "
        f"Request code {req['request_code']}."
    )


async def approve_payment_request(
    *, sender_phone: str, request_code: str | None = None
) -> str:
    parent = _require_verified(sender_phone, role="parent")
    if isinstance(parent, str):
        return parent

    row = _resolve_request_for_parent(parent, request_code, action="approve")
    if isinstance(row, str):
        return row

    # Lazy expiration check
    if is_expired(row):
        transition_status(
            row["id"],
            expected="pending_parent",
            new="expired",
            actor_user_id=parent["id"],
            event_message="Request expired before parent approved.",
        )
        kid = get_user_by_id(row["kid_user_id"])
        if kid:
            await _try_send(
                kid["phone"],
                f"The {row['service_name']} request expired before {parent['name']} approved it.",
            )
        return f"Request {row['request_code']} expired; no payment was made."

    # Auto-set payout destination from env default if missing on the kid record.
    kid = get_user_by_id(row["kid_user_id"])
    if kid and not (kid.get("payout_destination") or "").strip():
        if KID_DEFAULT_PAYOUT_DESTINATION:
            validation_error = validate_payout_destination(
                KID_DEFAULT_PAYOUT_DESTINATION,
                chain=PAYMENT_DEFAULT_CHAIN,
            )
            if validation_error:
                return f"Can't send this yet - {validation_error}"
            default_destination = KID_DEFAULT_PAYOUT_DESTINATION.strip()
            set_payout_destination(kid["id"], default_destination)
            kid["payout_destination"] = default_destination
        else:
            return (
                f"Can't send this yet - I don't have a payout destination for {kid['name']}. "
                "Set KID_DEFAULT_PAYOUT_DESTINATION or PAYMENT_DEMO_TARGET to a real "
                "recipient, restart, then approve again."
            )

    ok = transition_status(
        row["id"],
        expected="pending_parent",
        new="approved",
        actor_user_id=parent["id"],
        parent_user_id=parent["id"],
        event_message="Parent approved payment request.",
    )
    if not ok:
        latest = get_payment_request_by_id(row["id"])
        return _status_sentence(latest) if latest else "ERROR: request not found."

    kid = get_user_by_id(row["kid_user_id"])
    if kid:
        await _try_send(
            kid["phone"],
            f"Approved — I'm sending {_format_money(row['amount_cents'], row['currency'])} "
            f"for {row['service_name']} now.",
        )

    return await execute_approved_payment(row["id"], actor_user_id=parent["id"])


async def decline_payment_request(
    *, sender_phone: str, request_code: str | None = None
) -> str:
    parent = _require_verified(sender_phone, role="parent")
    if isinstance(parent, str):
        return parent

    row = _resolve_request_for_parent(parent, request_code, action="decline")
    if isinstance(row, str):
        return row

    ok = transition_status(
        row["id"],
        expected="pending_parent",
        new="declined",
        actor_user_id=parent["id"],
        parent_user_id=parent["id"],
        event_message="Parent declined payment request.",
    )
    if not ok:
        latest = get_payment_request_by_id(row["id"])
        return _status_sentence(latest) if latest else "ERROR: request not found."

    kid = get_user_by_id(row["kid_user_id"])
    if kid:
        await _try_send(
            kid["phone"],
            f"{parent['name']} declined the {row['service_name']} request.",
        )

    return f"Declined request {row['request_code']}."


async def get_payment_request_status(
    *, sender_phone: str, request_code: str | None = None
) -> str:
    user = _require_verified(sender_phone)
    if isinstance(user, str):
        return user

    code = _normalize_code(request_code)
    if code:
        row = get_payment_request_by_code(user["family_id"], code)
        if not row:
            return f"No request found for code {code}."
    else:
        pending = list_pending_requests_for_family(user["family_id"])
        if not pending:
            return "No active payment requests."
        if len(pending) > 1:
            codes = ", ".join(r["request_code"] for r in pending)
            return f"Multiple active requests: {codes}. Ask about one code."
        row = pending[0]

    if user["role"] == "kid" and row["kid_user_id"] != user["id"]:
        return "ERROR: that request belongs to a different kid."

    return _status_sentence(row)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def execute_approved_payment(
    req_id: int, *, actor_user_id: int | None = None
) -> str:
    # approved -> executing in a single atomic transition
    ok = transition_status(
        req_id,
        expected="approved",
        new="executing",
        actor_user_id=actor_user_id,
        event_message="Payment execution started.",
    )
    if not ok:
        latest = get_payment_request_by_id(req_id)
        return _status_sentence(latest) if latest else "ERROR: request not found."

    row = get_payment_request_by_id(req_id)
    if not row:
        return "ERROR: request not found after marking executing."

    kid = get_user_by_id(row["kid_user_id"])
    destination = ((kid or {}).get("payout_destination") or KID_DEFAULT_PAYOUT_DESTINATION).strip()

    validation_error = validate_payout_destination(
        destination,
        chain=PAYMENT_DEFAULT_CHAIN,
    )
    if validation_error:
        reason = (
            f"{validation_error} Update the env var, restart, and create a new request."
        )
        transition_status(
            req_id,
            expected="executing",
            new="failed",
            actor_user_id=actor_user_id,
            failure_reason=reason,
            event_message=reason,
        )
        await _notify_kid_failed(row, reason)
        return f"Approved, but payment could not run: {reason}"

    try:
        # Sponge SDK is sync — run in a thread so we don't block the event loop
        # (which would stall every other webhook for the duration of the transfer).
        result = await asyncio.to_thread(
            send_funds,
            to=destination,
            amount_cents=row["amount_cents"],
            currency=row["currency"],
            memo=row["service_name"],
        )
    except SpongePaymentError as e:
        reason = str(e)
        transition_status(
            req_id,
            expected="executing",
            new="failed",
            actor_user_id=actor_user_id,
            failure_reason=reason,
            event_message=reason,
        )
        await _notify_kid_failed(row, reason)
        return f"Approved, but Sponge could not complete the payment: {reason}"
    except Exception as e:
        log.exception("Sponge send failed for request %s", req_id)
        reason = f"{type(e).__name__}: {e}"
        transition_status(
            req_id,
            expected="executing",
            new="failed",
            actor_user_id=actor_user_id,
            failure_reason=reason,
            event_message="Sponge send raised.",
        )
        await _notify_kid_failed(row, "Sponge send failed.")
        return "Approved, but Sponge could not complete the payment."

    reference = str(result.get("reference") or "")
    transition_status(
        req_id,
        expected="executing",
        new="paid",
        actor_user_id=actor_user_id,
        sponge_reference=reference,
        metadata=result.get("raw") if isinstance(result, dict) else None,
        event_message="Sponge payment completed.",
    )

    if kid:
        await _try_send(
            kid["phone"],
            f"{_format_money(row['amount_cents'], row['currency'])} sent to your account "
            f"for {row['service_name']}. Ref: {reference or 'n/a'}",
        )

    return (
        f"Paid {_format_money(row['amount_cents'], row['currency'])} to "
        f"{kid['name'] if kid else 'kid'} for {row['service_name']}. "
        f"Ref: {reference or 'n/a'}"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _require_verified(sender_phone: str, *, role: str | None = None) -> dict | str:
    user = get_user_by_phone(sender_phone)
    if not user:
        return "ERROR: sender is not registered."
    if user["onboarding_state"] != "verified":
        return f"ERROR: {user['name']} is not verified yet."
    if role and user["role"] != role:
        return (
            f"ERROR: {user['name']} is a {user['role']}, not a {role}."
        )
    return user


def _resolve_request_for_parent(
    parent: dict, request_code: str | None, *, action: str
) -> dict | str:
    code = _normalize_code(request_code)
    if code:
        row = get_payment_request_by_code(parent["family_id"], code)
        if not row:
            return f"No request found for code {code}."
        if row["status"] != "pending_parent":
            return _status_sentence(row)
        return row

    pending = list_pending_requests_for_family(parent["family_id"])
    if not pending:
        return f"No pending request to {action}."
    if len(pending) > 1:
        codes = ", ".join(r["request_code"] for r in pending)
        return f"Multiple pending requests ({codes}); include the request code."
    return pending[0]


async def _notify_kid_failed(row: dict, reason: str) -> None:
    kid = get_user_by_id(row["kid_user_id"])
    if not kid:
        return
    await _try_send(
        kid["phone"],
        f"The payment was approved but didn't go through. {reason}",
    )


async def _try_send(to_number: str, body: str) -> None:
    try:
        await send_message(to_number, body)
    except Exception:
        log.exception("Failed to send payment notification to %s", to_number)


def _normalize_code(code: str | None) -> str:
    if not code:
        return ""
    digits = "".join(c for c in str(code) if c.isdigit())
    return digits if len(digits) == 6 else ""


def _format_money(amount_cents: int, currency: str) -> str:
    amount = Decimal(amount_cents) / Decimal(100)
    if currency == "USD":
        return f"${amount:.2f}"
    return f"{currency} {amount:.2f}"


def _status_sentence(row: dict) -> str:
    amount = _format_money(row["amount_cents"], row["currency"])
    base = (
        f"Request {row['request_code']} is {row['status']}: "
        f"{amount} for {row['service_name']}."
    )
    if row["status"] == "paid" and row.get("sponge_reference"):
        return f"{base} Ref: {row['sponge_reference']}"
    if row["status"] == "failed" and row.get("failure_reason"):
        return f"{base} Reason: {row['failure_reason']}"
    return base
