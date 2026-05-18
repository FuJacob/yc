import json
import logging
from typing import Any

from agentphone_client import send_message
from browser_agent import check_d2l_grades, create_d2l_session, stream_until_done
from config import (
    KID_DEFAULT_PAYOUT_DESTINATION,
    PUBLIC_URL,
)
from db import (
    create_family_with_users,
    delete_family,
    get_kid_for_parent,
    get_parent_for_kid,
    get_user_by_phone,
    set_onboarding_state,
    set_payout_destination,
)
import memory
from payment_service import (
    approve_payment_request,
    create_payment_request,
    decline_payment_request,
    get_payment_request_status,
)


# Names the LLM sometimes invents when the user hasn't actually given one.
# Reject these to prevent garbage onboarding records.
_PLACEHOLDER_NAMES = {
    "unknown", "n/a", "na", "none", "null", "parent", "kid", "child",
    "user", "anon", "anonymous", "guest", "test", "tbd", "?", "",
}


def _is_placeholder(name: str) -> bool:
    return name.strip().lower() in _PLACEHOLDER_NAMES

log = logging.getLogger(__name__)


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "register_family",
            "description": (
                "Register a new family. Call this when a sender unknown to the system "
                "provides their own first name, their kid's first name, and their kid's "
                "phone number. The tool creates the family record and texts the kid for "
                "verification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_name": {
                        "type": "string",
                        "description": "First name of the sender (the parent).",
                    },
                    "kid_name": {
                        "type": "string",
                        "description": "First name of the kid.",
                    },
                    "kid_phone": {
                        "type": "string",
                        "description": "Kid's phone number. Accept any format; will be normalized to E.164.",
                    },
                },
                "required": ["parent_name", "kid_name", "kid_phone"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_kid",
            "description": (
                "Mark a kid as verified. Call this when a kid whose onboarding_state is "
                "'pending_verification' replies affirmatively (yes, yeah, yep, sure, ok, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kid_phone": {
                        "type": "string",
                        "description": "The kid's phone number — i.e. the current sender's phone.",
                    }
                },
                "required": ["kid_phone"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_d2l_grades",
            "description": (
                "Look up the kid's current grades on the school portal (Waterloo D2L). "
                "Call this only when a VERIFIED parent asks about their kid's grades, "
                "assignments, or school performance. The browser run takes ~20-40 seconds."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "student_name": {
                        "type": "string",
                        "description": "First name of the kid whose grades to check.",
                    }
                },
                "required": ["student_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": (
                "Store a durable fact about this family for future conversations. "
                "Use when the user shares info worth remembering: school/program, "
                "current courses, tutors, recurring schedules, preferences, etc. "
                "Do NOT use for ephemeral things ('I'm tired today')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact to remember, written as a complete sentence.",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["school_info", "preference", "relationship", "approval", "other"],
                        "description": "What kind of fact this is.",
                    },
                    "kid_name": {
                        "type": "string",
                        "description": "Name of the kid this fact is about, if any. Omit if the fact is about the parent or family as a whole.",
                    },
                },
                "required": ["content", "category"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Search memories for past facts about this family. Use when the user "
                "asks about something you might have heard before ('what's his tutor "
                "schedule?', 'anything you remember about Gabe?'). Returns a list of "
                "matching memories or an empty result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Short natural-language description of what to look up.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unregister_family",
            "description": (
                "Delete the sender's family record entirely (parent + kid rows). "
                "ONLY call this when a verified parent explicitly asks to start over, "
                "unregister, delete their account, or remove their kid. After this, "
                "the sender becomes UNKNOWN again and can re-register from scratch."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_payment_request",
            "description": (
                "Create a parent-approval payment request. Call this only for a "
                "VERIFIED kid asking to pay for one specific service. The amount "
                "must be integer cents; never pass a float amount."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Specific service or thing the kid wants to pay for.",
                    },
                    "amount_cents": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Requested amount in integer cents, e.g. $2.00 -> 200.",
                    },
                    "currency": {
                        "type": "string",
                        "description": "Currency code. Default USD.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Kid's short reason or context for the request.",
                    },
                },
                "required": ["service_name", "amount_cents"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_payment_request",
            "description": (
                "Approve a pending payment request. Call this only for a VERIFIED "
                "parent in the same family. If the parent omits a code, the tool "
                "will approve only when exactly one pending request exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_code": {
                        "type": "string",
                        "description": "6-digit payment request code, if provided.",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decline_payment_request",
            "description": (
                "Decline a pending payment request. Call this only for a VERIFIED "
                "parent in the same family."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_code": {
                        "type": "string",
                        "description": "6-digit payment request code, if provided.",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_payment_request_status",
            "description": (
                "Get the status of a payment request for a verified parent or the "
                "kid who created it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_code": {
                        "type": "string",
                        "description": "6-digit payment request code, if provided.",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
]


async def dispatch_tool(
    name: str, args: dict[str, Any], *, sender_phone: str, ctx: dict
) -> str:
    """Run the named tool and return a string result for the LLM."""
    log.info("Tool call name=%s args=%s sender=%s", name, args, sender_phone)

    if name == "register_family":
        return await _register_family(
            sender_phone=sender_phone,
            parent_name=str(args.get("parent_name", "")).strip(),
            kid_name=str(args.get("kid_name", "")).strip(),
            kid_phone=normalize_phone(str(args.get("kid_phone", ""))),
            ctx=ctx,
        )

    if name == "confirm_kid":
        return await _confirm_kid(
            kid_phone=normalize_phone(str(args.get("kid_phone", "")))
        )

    if name == "check_d2l_grades":
        student_name = str(args.get("student_name", "")).strip()
        result = await _dispatch_check_grades(sender_phone, student_name, ctx)
        return result

    if name == "remember_fact":
        content = str(args.get("content", "")).strip()
        category = str(args.get("category", "other")).strip() or "other"
        kid_name = str(args.get("kid_name", "")).strip() or None
        family_id = ctx.get("family_id")
        if family_id is None:
            return "ERROR: cannot remember — sender has no family registered yet."
        if not content:
            return "ERROR: content is empty."
        md: dict = {"category": category, "source": "user_message"}
        if kid_name:
            md["kid_name"] = kid_name
        ok = await memory.remember(family_id, content, md)
        return "stored" if ok else "memory currently unavailable (continuing without)"

    if name == "recall":
        query = str(args.get("query", "")).strip()
        family_id = ctx.get("family_id")
        if family_id is None:
            return "no memories — sender has no family yet."
        if not query:
            return "ERROR: query is empty."
        results = await memory.recall(family_id, query)
        if not results:
            return "no relevant memories found."
        return memory.format_memories_block(results) or "no relevant memories found."

    if name == "unregister_family":
        sender = get_user_by_phone(sender_phone)
        if not sender:
            return "ERROR: you're not registered. Nothing to delete."
        if sender["role"] != "parent":
            return "ERROR: only the parent can unregister the family."
        deleted = delete_family(sender["family_id"])
        ctx["family_id"] = None
        log.info(
            "unregister_family deleted %d rows for family=%s (parent=%s)",
            deleted,
            sender["family_id"],
            sender_phone,
        )
        return (
            f"Family deleted ({deleted} rows). The parent and kid records are "
            f"both gone. The sender can now register from scratch."
        )

    if name == "create_payment_request":
        amount_cents = args.get("amount_cents")
        if isinstance(amount_cents, str) and amount_cents.isdigit():
            amount_cents = int(amount_cents)
        return await create_payment_request(
            sender_phone=sender_phone,
            service_name=str(args.get("service_name", "")).strip(),
            amount_cents=amount_cents,
            currency=str(args.get("currency") or "USD").strip(),
            reason=str(args.get("reason", "")).strip(),
        )

    if name == "approve_payment_request":
        return await approve_payment_request(
            sender_phone=sender_phone,
            request_code=str(args.get("request_code") or "").strip() or None,
        )

    if name == "decline_payment_request":
        return await decline_payment_request(
            sender_phone=sender_phone,
            request_code=str(args.get("request_code") or "").strip() or None,
        )

    if name == "get_payment_request_status":
        return await get_payment_request_status(
            sender_phone=sender_phone,
            request_code=str(args.get("request_code") or "").strip() or None,
        )

    return f"ERROR: unknown tool '{name}'"


async def _register_family(
    *,
    sender_phone: str,
    parent_name: str,
    kid_name: str,
    kid_phone: str,
    ctx: dict,
) -> str:
    if not parent_name or not kid_name or not kid_phone:
        return "ERROR: missing parent_name, kid_name, or kid_phone. Ask the user for the missing field."

    if _is_placeholder(parent_name):
        return (
            f"ERROR: '{parent_name}' is not a real first name. Ask the user "
            f"for their actual first name before calling register_family."
        )
    if _is_placeholder(kid_name):
        return (
            f"ERROR: '{kid_name}' is not a real kid's name. Ask the user "
            f"for the kid's actual first name."
        )

    if kid_phone == sender_phone:
        return "ERROR: the kid's phone number can't be the same as the parent's."

    existing = get_user_by_phone(sender_phone)
    if existing:
        return f"ERROR: sender is already registered as {existing['name']} ({existing['role']})."

    existing_kid = get_user_by_phone(kid_phone)
    if existing_kid:
        return f"ERROR: phone {kid_phone} is already registered as {existing_kid['name']}."

    family_id, parent_id, kid_id = create_family_with_users(
        parent_name=parent_name,
        parent_phone=sender_phone,
        kid_name=kid_name,
        kid_phone=kid_phone,
    )

    # Make memory tools work for subsequent tool calls in the same message.
    ctx["family_id"] = family_id

    # Auto-set kid's payout destination if configured in env.
    if KID_DEFAULT_PAYOUT_DESTINATION:
        set_payout_destination(kid_id, KID_DEFAULT_PAYOUT_DESTINATION)

    # Seed an initial family memory (no-op if Supermemory disabled).
    from datetime import datetime, timezone
    memory.fire_and_forget(
        memory.remember(
            family_id,
            f"Family registered on {datetime.now(timezone.utc).date().isoformat()}: "
            f"parent={parent_name}, kid={kid_name}.",
            {"category": "school_info", "kid_name": kid_name, "source": "registration"},
        )
    )

    try:
        await send_message(
            to_number=kid_phone,
            body=(
                f"Hey {kid_name}, your parent {parent_name} just set you up with "
                f"Riley so they can help with school stuff like checking your grades. "
                f"Is this you? Just let me know and you're all set."
            ),
        )
    except Exception as e:
        log.exception("Failed to send verification text to kid")
        return (
            f"Family created (family_id={family_id}), but failed to send the "
            f"verification text to {kid_phone}: {e}"
        )

    return (
        f"Family registered (family_id={family_id}). Parent {parent_name} "
        f"(id={parent_id}) auto-verified. Kid {kid_name} (id={kid_id}) is now "
        f"pending verification — once {kid_name} confirms via text, they're all set."
    )


async def _confirm_kid(*, kid_phone: str) -> str:
    kid = get_user_by_phone(kid_phone)
    if not kid:
        return f"ERROR: no user found with phone {kid_phone}."
    if kid["role"] != "kid":
        return f"ERROR: {kid['name']} is registered as {kid['role']}, not kid."
    if kid["onboarding_state"] == "verified":
        return f"{kid['name']} is already verified."

    set_onboarding_state(kid["id"], "verified")

    parent = get_parent_for_kid(kid["id"])
    if parent:
        try:
            await send_message(
                to_number=parent["phone"],
                body=(
                    f"{kid['name']} confirmed — you're all set! You can ask me things like "
                    f"\"how are {kid['name']}'s grades looking?\""
                ),
            )
        except Exception:
            log.exception("Failed to notify parent of verification")

    return f"{kid['name']} verified. Parent has been notified."


MAX_STEP_MESSAGES = 5


async def _dispatch_check_grades(sender_phone: str, student_name: str, ctx: dict) -> str:
    """Create cloud browser session, send live link, stream steps, return grades."""
    live_sessions = ctx.get("live_sessions")

    # 1. Create session + task — returns instantly with live_url + task_response.
    #    task_response is what stream_until_done needs to emit step updates;
    #    discarding it (as the previous code did) silently kills on_step.
    task_id, session_id, live_url, task_response = await create_d2l_session(student_name)

    # 2. Register for /live/{session_id} route
    if live_sessions is not None and live_url:
        live_sessions[session_id] = live_url

    # 3. Build the live view link
    live_view_url = f"{PUBLIC_URL}/live/{session_id}" if live_url else None

    # 4. Send live link to parent immediately (best-effort, don't block on failure)
    if live_view_url:
        try:
            await send_message(
                to_number=sender_phone,
                body=f"Checking now — watch live: {live_view_url}",
            )
        except Exception:
            log.exception("Failed to send live link")

    # 5. Stream steps as iMessages
    steps_sent = 0

    async def on_step(summary: str):
        nonlocal steps_sent
        if steps_sent < MAX_STEP_MESSAGES:
            try:
                await send_message(to_number=sender_phone, body=summary)
                steps_sent += 1
            except Exception:
                log.exception("Failed to send step update")

    # 5. Block until done, streaming intermediate steps via on_step.
    result = await stream_until_done(
        task_id, task_response=task_response, on_step=on_step
    )

    ctx["notify_kid_about_grades"] = True

    # Fire-and-forget grade snapshot to memory (no-op if Supermemory disabled).
    family_id = ctx.get("family_id")
    if family_id is not None:
        memory.fire_and_forget(
            memory.snapshot_grades(family_id, student_name, result)
        )

    # Include live link in tool result so the LLM can mention it in its reply
    if live_view_url:
        return f"LIVE VIEW: {live_view_url}\n\n{result}"
    return result


def normalize_phone(phone: str) -> str:
    """Crude E.164 normalization for North American numbers."""
    if not phone:
        return phone
    cleaned = "".join(c for c in phone if c.isdigit() or c == "+")
    if cleaned.startswith("+"):
        return cleaned
    if len(cleaned) == 10:
        return "+1" + cleaned
    if len(cleaned) == 11 and cleaned.startswith("1"):
        return "+" + cleaned
    return cleaned
