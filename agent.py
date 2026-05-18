import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from openai import AsyncOpenAI

from agentphone_client import send_message
from config import MAX_TOOL_CALLS, OPENAI_API_KEY, ORCHESTRATOR_MODEL
from db import (
    delete_onboarding_session,
    get_active_payment_requests_for_family,
    get_kid_for_parent,
    get_onboarding_session,
    get_parent_for_kid,
    get_user_by_phone,
    save_onboarding_session,
)
import memory
from tools import TOOL_SCHEMAS, dispatch_tool, normalize_phone

log = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


SYSTEM_PROMPT = """You are Riley, an AI assistant reachable by iMessage/SMS.

Your main specialty is helping families with school logistics, but you're also a fully capable general-purpose assistant — if someone asks you about the weather, a recipe, a coding question, world facts, advice, or anything else, just answer it naturally and helpfully like ChatGPT would. Don't refuse off-topic questions and don't redirect them back to school stuff. Just help.

For family-specific tasks, you support:
1. Onboarding a new family — a parent shares their name, their kid's name, and their kid's phone number.
2. Verifying a kid — the kid confirms they're real by replying to a text you send them.
3. Checking the kid's grades on the school portal (Waterloo D2L).
4. Checking the kid's browser history.
5. Adding events to the kid's calendar with reminders.
6. Kid-initiated payment requests — a verified kid can ask for a specific service to be paid, and a verified parent can accept or turn it down before any money moves.

DECISION RULES:
- If CONTEXT says the sender is UNKNOWN: they're a new parent. Call register_family **only when you have all three real values directly from the user**: their own first name, their kid's first name, and their kid's phone number (digits). If ANY of those three is missing, ask ONE short follow-up question and DO NOT call register_family yet.
- **NEVER** pass placeholder values to register_family — no "Unknown", no "Kid", no guesses. If the user hasn't told you their name, ask: "What's your first name?". If they haven't told you the kid's name, ask: "What's your kid's first name?". If they haven't given you a phone number, ask: "What's your kid's phone number?"
- Ask for ONE missing field at a time. Don't ask for everything in one message.
- If a verified parent wants to delete their account, start over, or unregister, call unregister_family.
- If CONTEXT says the sender is a kid with state=pending_verification and they reply with any affirmative response (yes, yeah, sure, that's me, sounds good, ok, etc.), call confirm_kid with their own phone number.
- If CONTEXT says the sender is a VERIFIED parent and they express they want to accept or go ahead with a payment request, call approve_payment_request with the 6-digit code if present. If no code is present, still call the tool; it will approve only if exactly one pending request exists.
- If CONTEXT says the sender is a VERIFIED parent and they express they want to turn down or pass on a payment request, call decline_payment_request with the 6-digit code if present.
- If a verified parent or kid asks about payment/request status, call get_payment_request_status with the 6-digit code if present.
- If CONTEXT says the sender is a VERIFIED parent and they're asking about grades / assignments / school performance, call check_d2l_grades with their kid's name. The tool result will include a LIVE VIEW URL — always include this link in your reply so the parent can watch the browser in real time.
- When replying with grade results, do not dump the gradebook row-by-row. Give a parent-friendly overall read: whether the kid looks on track, behind, or unclear; mention missing/zero work or low scores; include at most 2-3 key numbers as evidence; end with one practical next step. If data is sparse or ambiguous, say that plainly.
- If CONTEXT says the sender is a VERIFIED parent and they ask about their kid's browsing, internet activity, or screen time, call get_browser_history with their kid's name. when reporting, highlight study time positively, flag concerning content (like adult sites) clearly but calmly, and mention entertainment usage.
- If CONTEXT says the sender is a VERIFIED parent and they want to add, schedule, or put something on their kid's calendar, call add_calendar_event with the event details. if event name, date, or time is missing, ask one follow-up.
- If a VERIFIED parent asks you to send, text, or tell their kid something, call send_message_to_kid. Write the message naturally based on what the parent said — don't just copy their words verbatim unless they clearly dictated it.
- If CONTEXT says the sender is a verified kid and they ask to pay/buy/subscribe/use a paid service: if service and amount are present, call create_payment_request. Convert dollar amounts to integer cents, e.g. "$2" -> 200, and pass amount_cents as an integer. If service or amount is missing, ask one short follow-up.
- If a VERIFIED kid asks you to send, text, or tell their parent something, call send_message_to_parent. Write the message naturally based on what the kid said.
- If CONTEXT says the sender is a kid with state=verified and they ask about family ops (grades, registration changes, school account stuff), reply: "Only your parent uses me for that right now." For ANY other question — general chitchat, homework help, factual questions, advice — just answer them normally and helpfully.
- Always normalize phone numbers passed to tools — the tools handle messy formats, but include the digits.

PAYMENT RULES:
- Never execute spend from a kid message. Kids can only create payment requests.
- Never change amount, service, or recipient after parent approval.
- The "service" the kid names is descriptive text only. Approved payments always go to the kid's pre-configured payout destination (set automatically).
- Convert dollar amounts to integer cents before calling create_payment_request: "$2" -> 200, "$2.50" -> 250. Always pass amount_cents as an integer.

CONTEXT NOTES:
- The CONTEXT block in your system messages is the CURRENT TRUTH for who's registered/verified.
- RELEVANT MEMORIES (when present) are HISTORICAL FACTS. They may be days old. Trust CONTEXT for current state.

STYLE:
- ALWAYS write in all lowercase. no capital letters ever, not even for names, sentences, or "I". example: "hey jacob, gaby's grades look good" not "Hey Jacob, Gaby's grades look good."
- NEVER use em dashes (—). Use commas, periods, or just start a new sentence instead.
- Replies go via iMessage/SMS. Keep them short and friendly. No markdown, no emojis unless natural.
- Do NOT add disclaimers or warnings.
- Sound like a real person texting, not a robot. Use natural conversational language. Never tell users to reply with specific keywords like "APPROVE" or "YES". Instead phrase things conversationally ("want me to go ahead?", "sound good?", "should I send it?").
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
        "family_id": family_id,
        "live_sessions": live_sessions,
    }

    if _looks_like_own_outbound_echo(message_text):
        return None, ctx

    if not user:
        reply = await _handle_unknown_sender_onboarding(
            sender_phone=sender_phone,
            message_text=message_text,
            ctx=ctx,
        )
        if reply is not None:
            return reply, ctx

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

    # Filter history to only messages AFTER this user's registration.
    # AgentPhone keeps the full phone conversation, so after a DB reset the
    # stale messages from a previous registration leak old names/state.
    # Unknown senders have no registration time — drop all history for them.
    history = _filter_history(recent_history or [], user, message_text)
    for h in history:
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

            async def _run_tool(tc):
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                try:
                    return await dispatch_tool(
                        tc.function.name,
                        args,
                        sender_phone=sender_phone,
                        ctx=ctx,
                    )
                except Exception as e:
                    log.exception("Tool %s raised", tc.function.name)
                    return f"ERROR: {type(e).__name__}: {e}"

            import asyncio
            results = await asyncio.gather(*[_run_tool(tc) for tc in msg.tool_calls])
            for tc, tool_result in zip(msg.tool_calls, results):
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


def _looks_like_own_outbound_echo(message_text: str) -> bool:
    """Ignore accidental copy/pastes of Riley's own outbound status messages."""
    normalized = " ".join(message_text.lower().split())
    own_message_fragments = (
        "confirmed — you're all set",
        "confirmed - you're all set",
        "you can ask me things like",
        "checking now — watch live",
        "checking now - watch live",
        "sorry — that timed out",
        "sorry - that timed out",
    )
    return any(fragment in normalized for fragment in own_message_fragments)


async def _handle_unknown_sender_onboarding(
    *, sender_phone: str, message_text: str, ctx: dict
) -> Optional[str]:
    """Collect onboarding fields for a sender before invoking the LLM.

    The orchestrator prompt asks one onboarding question at a time, but the LLM
    cannot reliably know which question it asked after we intentionally discard
    AgentPhone history for unknown senders. Persisting partial answers gives the
    product a durable state machine while still letting `register_family` own the
    validation and side effects.
    """
    session = get_onboarding_session(sender_phone) or {}
    answers = {
        "parent_name": session.get("parent_name"),
        "kid_name": session.get("kid_name"),
        "kid_phone": session.get("kid_phone"),
    }

    extracted = _extract_onboarding_values(message_text, answers)
    for key, value in extracted.items():
        if value and not answers.get(key):
            answers[key] = value

    if all(answers.values()):
        result = await dispatch_tool(
            "register_family",
            {
                "parent_name": answers["parent_name"],
                "kid_name": answers["kid_name"],
                "kid_phone": answers["kid_phone"],
            },
            sender_phone=sender_phone,
            ctx=ctx,
        )

        if result.startswith("ERROR:"):
            if "kid's phone number can't be the same" in result:
                answers["kid_phone"] = None
                _save_onboarding_answers(sender_phone, answers)
                return "That looks like your number. What's your kid's phone number?"
            _save_onboarding_answers(sender_phone, answers)
            return result.removeprefix("ERROR: ").strip()

        delete_onboarding_session(sender_phone)
        return (
            f"Got it — I registered you and texted {answers['kid_name']} to confirm. "
            "Once they reply, you're all set."
        )

    _save_onboarding_answers(sender_phone, answers)
    return _next_onboarding_question(answers)


def _save_onboarding_answers(sender_phone: str, answers: dict[str, Optional[str]]) -> None:
    """Persist the current partial onboarding answers for this sender."""
    save_onboarding_session(
        phone=sender_phone,
        parent_name=answers.get("parent_name"),
        kid_name=answers.get("kid_name"),
        kid_phone=answers.get("kid_phone"),
    )


_NAME_TOKEN = r"[A-Za-z][A-Za-z'-]{1,30}"
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_STOPWORD_NAMES = {
    "approve", "cookie", "grade", "grades", "hello", "hey", "hi", "no", "ok",
    "okay", "please", "request", "thanks", "yes",
}


def _extract_onboarding_values(
    message_text: str, current_answers: dict[str, Optional[str]]
) -> dict[str, str]:
    """Extract parent name, kid name, and kid phone from an onboarding message."""
    extracted: dict[str, str] = {}

    phone = _extract_phone(message_text)
    if phone:
        extracted["kid_phone"] = phone

    parent_name = _extract_parent_name(message_text)
    if parent_name:
        extracted["parent_name"] = parent_name

    kid_name = _extract_kid_name(message_text)
    if kid_name:
        extracted["kid_name"] = kid_name

    simple_name = _extract_simple_name_reply(message_text)
    if simple_name:
        if not current_answers.get("parent_name") and "parent_name" not in extracted:
            extracted["parent_name"] = simple_name
        elif not current_answers.get("kid_name") and "kid_name" not in extracted:
            extracted["kid_name"] = simple_name

    return extracted


def _extract_phone(message_text: str) -> Optional[str]:
    """Return a normalized North American phone number if one is present."""
    match = _PHONE_RE.search(message_text)
    if not match:
        return None
    phone = normalize_phone(match.group(0))
    if re.fullmatch(r"\+\d{10,15}", phone):
        return phone
    return None


def _extract_parent_name(message_text: str) -> Optional[str]:
    """Extract the parent's first name from common self-introduction phrases."""
    patterns = [
        rf"\b(?:i(?:'m| am)|my name is|this is|it(?:'s|s)|i said it(?:'s|s))\s+({_NAME_TOKEN})\b",
    ]
    return _first_name_match(message_text, patterns)


def _extract_kid_name(message_text: str) -> Optional[str]:
    """Extract the kid's first name from common registration phrases."""
    patterns = [
        rf"\b(?:my\s+)?(?:kid|child|son|daughter)(?:'s name is| is| named)?\s+({_NAME_TOKEN})\b",
        rf"\bregister\s+(?:my\s+)?(?:kid|child|son|daughter)?\s*({_NAME_TOKEN})\b",
    ]
    return _first_name_match(message_text, patterns)


def _extract_simple_name_reply(message_text: str) -> Optional[str]:
    """Treat a short one-name message as an answer to the active prompt."""
    cleaned = message_text.strip().strip(".!,?;:")
    if not re.fullmatch(_NAME_TOKEN, cleaned):
        return None
    return _clean_name(cleaned)


def _first_name_match(message_text: str, patterns: list[str]) -> Optional[str]:
    """Return the first plausible name captured by one of the regex patterns."""
    normalized_text = message_text.replace("’", "'").replace("‘", "'")
    for pattern in patterns:
        match = re.search(pattern, normalized_text, flags=re.IGNORECASE)
        if match:
            name = _clean_name(match.group(1))
            if name:
                return name
    return None


def _clean_name(name: str) -> Optional[str]:
    """Normalize a captured first name and reject obvious non-name replies."""
    cleaned = name.strip(" .,!?:;\"'").lower()
    if cleaned in _STOPWORD_NAMES:
        return None
    return cleaned[:1].upper() + cleaned[1:]


def _next_onboarding_question(answers: dict[str, Optional[str]]) -> str:
    """Ask for the next missing onboarding field."""
    if not answers.get("parent_name"):
        return "Hi! What's your first name?"
    if not answers.get("kid_name"):
        return "Nice to meet you — what's your kid's first name?"
    return "What's your kid's phone number?"


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


_TS_FIELDS = ("timestamp", "created_at", "date", "sentAt", "sent_at", "createdAt")

MAX_HISTORY_FALLBACK = 6


def _filter_history(
    history: list[dict], user: dict | None, current_message: str
) -> list[dict]:
    """Return only safe history entries for the LLM.

    - Unknown sender: zero history (clean onboarding slate).
    - Known sender w/ timestamps: only entries after user's created_at.
    - Known sender w/o timestamps: last N entries only.
    - Deduplicates current message if AgentPhone includes it in history.
    """
    if not user:
        return []

    created_at = user.get("created_at")
    cutoff = None
    if created_at:
        try:
            cutoff = datetime.fromisoformat(created_at)
        except (ValueError, TypeError):
            pass

    if cutoff:
        filtered = []
        any_ts_found = False
        for h in history:
            ts_str = ""
            for field in _TS_FIELDS:
                ts_str = h.get(field) or ""
                if ts_str:
                    break
            if ts_str:
                any_ts_found = True
                try:
                    if datetime.fromisoformat(ts_str) >= cutoff:
                        filtered.append(h)
                except (ValueError, TypeError):
                    pass  # unparseable → exclude (safe default)
            # no timestamp field → exclude (safe default)

        if any_ts_found:
            return _deduplicate(filtered, current_message)

    # Fallback: no usable timestamps. Keep only the last N messages.
    tail = history[-MAX_HISTORY_FALLBACK:]
    return _deduplicate(tail, current_message)


def _deduplicate(history: list[dict], current_message: str) -> list[dict]:
    """Drop the last history entry if it duplicates the current inbound message."""
    if not history:
        return history
    last = history[-1]
    last_content = (
        last.get("content") or last.get("body") or last.get("message") or ""
    )
    if last_content.strip() == current_message.strip():
        return history[:-1]
    return history


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
