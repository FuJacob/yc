import json
import logging
from decimal import Decimal
from typing import Optional

from openai import AsyncOpenAI

from agentphone_client import send_message
from config import MAX_TOOL_CALLS, OPENAI_API_KEY, ORCHESTRATOR_MODEL
from db import (
    get_active_payment_requests_for_family,
    get_kid_for_parent,
    get_parent_for_kid,
    get_user_by_phone,
)
import memory
from tools import TOOL_SCHEMAS, dispatch_tool

log = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


SYSTEM_PROMPT = """You are Riley, an AI assistant reachable by iMessage/SMS.

Your main specialty is helping families with school logistics, but you're also a fully capable general-purpose assistant — if someone asks you about the weather, a recipe, a coding question, world facts, advice, or anything else, just answer it naturally and helpfully like ChatGPT would. Don't refuse off-topic questions and don't redirect them back to school stuff. Just help.

For family-specific tasks, you support:
1. Onboarding a new family — a parent sends their name, their kid's name, and their kid's phone number.
2. Verifying a kid — the kid replies YES (or similar) to the verification text.
3. Checking the kid's grades on the school portal (Waterloo D2L).
4. Remembering family facts across conversations (school, courses, tutors, preferences).
5. Kid-initiated payment requests — a verified kid can ask for a specific service to be paid, and a verified parent can approve or decline before any money moves.

DECISION RULES:
- If CONTEXT says the sender is UNKNOWN: they're a new parent. Call register_family **only when you have all three real values directly from the user**: their own first name, their kid's first name, and their kid's phone number (digits). If ANY of those three is missing, ask ONE short follow-up question and DO NOT call register_family yet.
- **NEVER** pass placeholder values to register_family — no "Unknown", no "Kid", no guesses. If the user hasn't told you their name, ask: "What's your first name?". If they haven't told you the kid's name, ask: "What's your kid's first name?". If they haven't given you a phone number, ask: "What's your kid's phone number?"
- Ask for ONE missing field at a time. Don't ask for everything in one message.
- If a verified parent says "I'm done" / "delete me" / "start over" / "unregister", call unregister_family.
- If CONTEXT says the sender is a kid with state=pending_verification and they reply YES / yeah / yep / sure / ok / confirm / etc, call confirm_kid with their own phone number.
- If CONTEXT says the sender is a VERIFIED parent and they say approve/yes/pay for a payment request, call approve_payment_request with the 6-digit code if present. If no code is present, still call the tool; it will approve only if exactly one pending request exists.
- If CONTEXT says the sender is a VERIFIED parent and they say decline/no/cancel for a payment request, call decline_payment_request with the 6-digit code if present.
- If a verified parent or kid asks about payment/request status, call get_payment_request_status with the 6-digit code if present.
- If CONTEXT says the sender is a VERIFIED parent and they're asking about grades / assignments / school performance, call check_d2l_grades with their kid's name. The tool result will include a LIVE VIEW URL — always include this link in your reply so the parent can watch the browser in real time.
- If CONTEXT says the sender is a verified kid and they ask to pay/buy/subscribe/use a paid service: if service and amount are present, call create_payment_request. Convert dollar amounts to integer cents, e.g. "$2" -> 200, and pass amount_cents as an integer. If service or amount is missing, ask one short follow-up.
- If CONTEXT says the sender is a kid with state=verified and they ask about family ops (grades, registration changes, school account stuff), reply: "Only your parent uses me for that right now." For ANY other question — general chitchat, homework help, factual questions, advice — just answer them normally and helpfully.
- If the user shares a durable fact about their kid or themselves ("remember Gabe is in 2A CS", "his tutor is on Tuesdays", "I prefer terse replies"), call remember_fact with the content + a sensible category (school_info / preference / relationship / approval).
- If the user asks about something you might have heard before ("what's Gabe's tutor schedule", "anything you remember about him"), call recall with a short query string.
- Always normalize phone numbers passed to tools — the tools handle messy formats, but include the digits.

PAYMENT RULES:
- Never execute spend from a kid message. Kids can only create payment requests.
- Never change amount, service, or recipient after parent approval.
- The "service" the kid names is descriptive text only. Approved payments always go to the kid's pre-configured payout destination on their user record.
- If a verified PARENT says something like "send Alex's payments to <X>", "set payout to <X>", or "pay Alex at <X>", call set_kid_payout_destination with <X>.
- Convert dollar amounts to integer cents before calling create_payment_request: "$2" -> 200, "$2.50" -> 250. Always pass amount_cents as an integer.

CONTEXT NOTES:
- The CONTEXT block in your system messages is the CURRENT TRUTH for who's registered/verified.
- RELEVANT MEMORIES (when present) are HISTORICAL FACTS. They may be days old. Trust CONTEXT for current state.

STYLE:
- Replies go via iMessage/SMS. Keep them short and friendly. No markdown, no emojis unless natural.
- Do NOT add disclaimers or warnings.
- When you call a tool that does real work (especially check_d2l_grades), include a brief "checking now…" text in your message content so the user sees activity while the tool runs.
"""


async def handle_inbound(
    *,
    sender_phone: str,
    message_text: str,
    recent_history: Optional[list[dict]] = None,
    live_sessions: Optional[dict] = None,
) -> tuple[Optional[str], dict]:
    """Run the orchestrator loop. Returns (final reply text, side-effect ctx)."""

    user = get_user_by_phone(sender_phone)
    family_id = user["family_id"] if user else None
    ctx: dict = {
        "notify_kid_about_grades": False,
        "family_id": family_id,
        "live_sessions": live_sessions,
    }

    # Fire recall + build the DB context block. Both run in parallel —
    # memory.recall has its own timeout + always returns [] on error.
    import asyncio
    context_str, memories = await asyncio.gather(
        asyncio.to_thread(_build_context, sender_phone),
        memory.recall(family_id, message_text),
    )

    system_blocks = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"CONTEXT:\n{context_str}"},
    ]
    memories_block = memory.format_memories_block(memories)
    if memories_block:
        system_blocks.append({"role": "system", "content": memories_block})

    messages: list[dict] = system_blocks

    for h in recent_history or []:
        role = "assistant" if h.get("direction") == "outbound" else "user"
        content = h.get("content") or h.get("body") or h.get("message") or ""
        if content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": message_text})

    for iteration in range(MAX_TOOL_CALLS + 1):
        response = await _client.chat.completions.create(
            model=ORCHESTRATOR_MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if msg.tool_calls:
            # If the model wrote interstitial content alongside the tool call,
            # send it now so the user sees acknowledgement before the long-running
            # tool returns.
            interstitial = (msg.content or "").strip()
            if interstitial:
                try:
                    await send_message(sender_phone, interstitial)
                except Exception:
                    log.exception("Failed to send interstitial message")

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                try:
                    tool_result = await dispatch_tool(
                        tc.function.name,
                        args,
                        sender_phone=sender_phone,
                        ctx=ctx,
                    )
                except Exception as e:
                    log.exception("Tool %s raised", tc.function.name)
                    tool_result = f"ERROR: {type(e).__name__}: {e}"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    }
                )

            continue

        return msg.content, ctx

    log.warning("Hit MAX_TOOL_CALLS without final response")
    return "Sorry, I got stuck. Try again?", ctx


def _build_context(sender_phone: str) -> str:
    user = get_user_by_phone(sender_phone)
    if not user:
        return (
            f"Sender phone: {sender_phone}\n"
            f"Sender record: UNKNOWN — no row in users table. Treat as a new parent."
        )

    lines = [
        f"Sender phone: {sender_phone}",
        f"Sender record: name={user['name']}, role={user['role']}, state={user['onboarding_state']}",
    ]

    if user["role"] == "parent":
        kid = get_kid_for_parent(user["id"])
        if kid:
            lines.append(
                f"Their kid: name={kid['name']}, phone={kid['phone']}, "
                f"state={kid['onboarding_state']}"
            )
        else:
            lines.append("Their kid: (none registered)")
    elif user["role"] == "kid":
        parent = get_parent_for_kid(user["id"])
        if parent:
            lines.append(
                f"Their parent: name={parent['name']}, phone={parent['phone']}"
            )

    payment_lines = _build_payment_context(user["family_id"])
    if payment_lines:
        lines.extend(payment_lines)

    return "\n".join(lines)


def _build_payment_context(family_id: int) -> list[str]:
    requests = get_active_payment_requests_for_family(family_id)
    if not requests:
        return ["Active payment requests: none"]

    lines = ["Active payment requests:"]
    for row in requests[:5]:
        amount = Decimal(row["amount_cents"]) / Decimal(100)
        amount_text = (
            f"${amount:.2f}"
            if row["currency"] == "USD"
            else f"{row['currency']} {amount:.2f}"
        )
        lines.append(
            "- "
            f"code={row['request_code']}, status={row['status']}, "
            f"amount={amount_text}, service={row['service_name']}"
        )
    return lines
