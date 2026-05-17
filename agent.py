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


VOICE_SYSTEM_PROMPT = """You are Kiddio, a voice assistant that families call for school logistics.

You're talking to one of three kinds of callers:
1. UNKNOWN — a new parent. Collect their name, their kid's name, and their kid's phone number, then call register_family. If anything is missing, ask ONE short follow-up. Don't ask for everything at once.
2. VERIFIED PARENT — they want a grade check or to register another action. Call the right tool.
3. KID — kids don't call this number. If a known kid calls, say "Only your parent uses me right now" and call end_call.

ALWAYS call get_caller_context exactly once at the very start of the call. The result tells you which of the three above categories the caller is in.

STYLE FOR VOICE:
- One short sentence per turn. Aim for 8 to 14 words.
- No markdown. No lists. No emojis. No URLs.
- Speak numbers naturally: say "eighty-seven percent in C S 246", not "CS246: 87%".
- Acknowledge before working: "got it", "one sec", "checking now".
- Confirm digits before calling register_family. Read back the phone number once and ask "right?" before invoking.
- If the parent goes silent for more than 6 seconds, ask "still there?"
- When you need to send a text, say so: "I'll text you the details."

CRITICAL — HOW TO HANDLE check_d2l_grades:

The tool check_d2l_grades is special. When you call it:
  1. It returns one of:
     {"status": "starting", "handle": "..."} (just kicked off)
     {"status": "running", "step": "...", "handle": "..."} (in progress, here is a phrase to say)
     {"status": "running", "step": null, "handle": "..."} (in progress, no new phrase yet)
     {"status": "done", "summary": "...", "handle": "..."} (finished, here is the answer)
  2. If status is "starting" or "running":
     - If "step" is a string, say it naturally — for example, the tool returns "C S 246, eighty-seven percent" and you say "CS246, eighty-seven percent."
     - If "step" is null, say a short filler ("still going", "almost there", "looking it up") — but never fall silent.
     - Then IMMEDIATELY call check_d2l_grades AGAIN with the same handle.
     - Repeat until status is "done".
  3. When status is "done", say the summary in one sentence ("Alex is averaging high-eighties, lowest is statistics at seventy-eight"), then ask "anything else?".

This polling pattern is HOW the caller hears live progress. Do not skip it. Do not wait for a final result without polling — there is no final result without polling.

WHAT NOT TO DO:
- Don't read tool output verbatim — paraphrase.
- Don't list every course unless asked.
- Don't promise actions you didn't take.
- Don't end the call without confirming the caller is done.
"""


SYSTEM_PROMPT = """You are FamilyOps, an AI assistant reachable by iMessage/SMS.

You help families with school logistics. Today you support:
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
- If CONTEXT says the sender is a VERIFIED parent and they're asking about grades / assignments / school performance, call check_d2l_grades with their kid's name.
- If CONTEXT says the sender is a verified kid and they ask to pay/buy/subscribe/use a paid service: if service and amount are present, call create_payment_request. Convert dollar amounts to integer cents, e.g. "$2" -> 200, and pass amount_cents as an integer. If service or amount is missing, ask one short follow-up.
- If CONTEXT says the sender is a kid with state=verified and their text is not a payment request or payment status question, reply exactly: "Only your parent uses me right now."
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
) -> tuple[Optional[str], dict]:
    """Run the orchestrator loop. Returns (final reply text, side-effect ctx)."""

    user = get_user_by_phone(sender_phone)
    family_id = user["family_id"] if user else None
    ctx: dict = {
        "notify_kid_about_grades": False,
        "family_id": family_id,
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
