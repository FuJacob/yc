import asyncio
import json
import logging
from typing import Any

import voice_state
import narration
from agentphone_client import end_call as agentphone_end_call
from agentphone_client import send_message
from browser_agent import check_d2l_grades
from config import (
    BROWSER_USE_FETCH_TIMEOUT_SECONDS,
    KID_VERIFICATION_TIMEOUT_SECONDS,
    VOICE_NARRATION_INTERVAL_SECONDS,
)
from db import (
    create_family_with_users,
    get_kid_for_parent,
    get_parent_for_kid,
    get_user_by_phone,
    set_onboarding_state,
)

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
        )

    if name == "confirm_kid":
        return await _confirm_kid(
            kid_phone=normalize_phone(str(args.get("kid_phone", "")))
        )

    if name == "check_d2l_grades":
        result = await check_d2l_grades(str(args.get("student_name", "")).strip())
        # Signal to the orchestrator that we should notify the kid AFTER the
        # parent's reply has been sent.
        ctx["notify_kid_about_grades"] = True
        return result

    return f"ERROR: unknown tool '{name}'"


async def _register_family(
    *, sender_phone: str, parent_name: str, kid_name: str, kid_phone: str
) -> str:
    if not parent_name or not kid_name or not kid_phone:
        return "ERROR: missing parent_name, kid_name, or kid_phone."

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

    try:
        await send_message(
            to_number=kid_phone,
            body=(
                f"Hi {kid_name}, your parent {parent_name} just registered you with "
                f"FamilyOps so they can help with school stuff like checking your grades. "
                f"Reply YES to confirm this is you."
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
        f"pending verification — a YES reply from {kid_phone} will complete it."
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
                    f"{kid['name']} is verified. Try asking 'what are {kid['name']}'s grades?'"
                ),
            )
        except Exception:
            log.exception("Failed to notify parent of verification")

    return f"{kid['name']} verified. Parent has been notified."


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


# ============================================================================
# Voice tools (RFC-3)
#
# The voice agent is hosted by AgentPhone. It calls our tool endpoints via
# `call.tool_call` webhooks. We answer SYNCHRONOUSLY with the tool result.
# ============================================================================


VOICE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_caller_context",
            "description": (
                "Call this once at the start of every call to learn who the caller "
                "is. Returns the caller's role and family info, or 'UNKNOWN' if "
                "they're not registered yet."
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
            "name": "register_family",
            "description": (
                "Register a new family. Call this when an UNKNOWN caller has "
                "given their name, their kid's name, and their kid's phone number. "
                "Read the digits back to the caller and ask for confirmation BEFORE "
                "invoking — voice transcription mishears digits. The tool creates "
                "the family record and texts the kid for verification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "parent_name": {"type": "string"},
                    "kid_name": {"type": "string"},
                    "kid_phone": {"type": "string"},
                },
                "required": ["parent_name", "kid_name", "kid_phone"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for_kid_confirmation",
            "description": (
                "Block for up to timeout_seconds while the kid replies YES via SMS. "
                "Use right after register_family. Returns {confirmed: true, kid_name} "
                "if they replied, {confirmed: false} on timeout."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kid_phone": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "default": 45},
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
                "Check the kid's grades on D2L. CALL THIS REPEATEDLY IN A LOOP, "
                "passing the handle back on every call, until status is 'done'. "
                "Each call returns the next progress chunk to read aloud. Do NOT "
                "fall silent between calls — if step is null, say a short filler "
                "('still going', 'almost there') and call again immediately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "student_name": {
                        "type": "string",
                        "description": (
                            "First name of the kid. Required on the FIRST call. "
                            "Omit on subsequent calls when handle is passed."
                        ),
                    },
                    "handle": {
                        "type": "string",
                        "description": (
                            "Handle from a previous call. Pass on every subsequent "
                            "call to continue the same fetch."
                        ),
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handoff_to_sms",
            "description": (
                "Send the caller an SMS and signal that the voice work is done. "
                "Use when the caller wants something written down or a tool is "
                "taking too long."
            ),
            "parameters": {
                "type": "object",
                "properties": {"body": {"type": "string"}},
                "required": ["body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "end_call",
            "description": (
                "End the call gracefully. Only call after the caller has indicated "
                "they're done."
            ),
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
]


def _voice_caller_context(from_number: str) -> str:
    """Build a one-line context string for the model on get_caller_context."""
    if not from_number:
        return "UNKNOWN — no caller-ID available."

    user = get_user_by_phone(from_number)
    if not user:
        return f"UNKNOWN — caller {from_number} is not registered. Treat as new parent."

    parts = [
        f"name={user['name']}",
        f"role={user['role']}",
        f"state={user['onboarding_state']}",
    ]

    if user["role"] == "parent":
        kid = get_kid_for_parent(user["id"])
        if kid:
            parts.append(
                f"kid={kid['name']} (phone={kid['phone']}, state={kid['onboarding_state']})"
            )
        else:
            parts.append("kid=(none)")
    elif user["role"] == "kid":
        parent = get_parent_for_kid(user["id"])
        if parent:
            parts.append(f"parent={parent['name']}")

    return "Verified caller: " + ", ".join(parts)


async def _voice_wait_for_kid_confirmation(kid_phone: str, timeout_seconds: int) -> dict:
    """Poll the DB until the kid's onboarding flips to verified, or timeout."""
    kid_phone = normalize_phone(kid_phone)
    deadline = asyncio.get_event_loop().time() + max(1, timeout_seconds)
    while asyncio.get_event_loop().time() < deadline:
        kid = get_user_by_phone(kid_phone)
        if kid and kid["onboarding_state"] == "verified":
            return {"confirmed": True, "kid_name": kid["name"]}
        await asyncio.sleep(1)
    return {"confirmed": False}


async def _voice_check_d2l_grades(
    *, call_id: str, sender_phone: str, args: dict[str, Any]
) -> dict:
    """Polling tool — see VOICE_TOOL_SCHEMAS."""
    handle = args.get("handle")

    if handle:
        # Subsequent poll — read next chunk from voice_state
        return await voice_state.next_chunk(call_id)

    # First call — kick off background fetch
    student = str(args.get("student_name", "")).strip()
    if not student:
        return {"status": "error", "message": "student_name required on first call"}

    new_handle = voice_state.new_handle()
    await voice_state.start(call_id, new_handle, student, sender_phone)
    asyncio.create_task(
        _pump_browser_use_into_state(call_id, new_handle, sender_phone, student)
    )
    return {"status": "starting", "handle": new_handle}


async def _pump_browser_use_into_state(
    call_id: str, handle: str, sender_phone: str, student: str
) -> None:
    """Background pump: run browser_use, push narration phrases to voice_state."""
    fetch_task = asyncio.create_task(check_d2l_grades(student))
    filler_iter = narration.rotating_filler()
    deadline = (
        asyncio.get_event_loop().time() + BROWSER_USE_FETCH_TIMEOUT_SECONDS
    )

    while not fetch_task.done():
        if asyncio.get_event_loop().time() >= deadline:
            log.warning("Browser Use exceeded fetch timeout for call %s", call_id)
            fetch_task.cancel()
            await voice_state.finish(
                call_id,
                "Sorry, this took too long. I will text you the grades when they are ready.",
                status="error",
            )
            return

        # Push one filler phrase, then wait an interval for the fetch to finish.
        await voice_state.push_step(call_id, next(filler_iter))
        try:
            await asyncio.wait_for(
                asyncio.shield(fetch_task),
                timeout=VOICE_NARRATION_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            return
        except Exception:
            # fetch raised — caught below in fetch_task.result()
            break

    try:
        summary = fetch_task.result()
    except Exception:
        log.exception("Browser Use fetch failed for call %s", call_id)
        await voice_state.finish(
            call_id,
            "Sorry, I could not get the grades right now. I will text you when I can.",
            status="error",
        )
        return

    await voice_state.finish(call_id, summary)
    await _maybe_notify_kid_about_grades(sender_phone)


async def _maybe_notify_kid_about_grades(sender_phone: str) -> None:
    """Fire the standard kid-FYI SMS after a successful voice grade check."""
    try:
        parent = get_user_by_phone(sender_phone)
        if not parent or parent.get("role") != "parent":
            return
        kid = get_kid_for_parent(parent["id"])
        if not kid or kid.get("onboarding_state") != "verified":
            return
        await send_message(
            kid["phone"],
            f"FYI {parent['name']} just checked your grades.",
        )
    except Exception:
        log.exception("Failed to notify kid after voice grade check")


def _parse_args(raw_args: Any) -> dict:
    """AgentPhone may deliver tool args as dict or JSON string. Tolerate both."""
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str) and raw_args.strip():
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


async def dispatch_voice_tool(payload: dict) -> dict:
    """Synchronously handle a `call.tool_call` webhook event.

    Returns the JSON body we send back to AgentPhone.
    """
    data = payload.get("data") or {}
    call_id = data.get("call_id") or data.get("callId") or ""
    from_number = (
        data.get("from_number") or data.get("from") or data.get("caller") or ""
    )
    tool_call_id = data.get("tool_call_id") or data.get("toolCallId") or ""
    tool_name = data.get("tool_name") or data.get("toolName") or ""
    args = _parse_args(data.get("arguments") or data.get("args"))

    log.info(
        "voice tool_call call_id=%s tool=%s from=%s args=%s",
        call_id, tool_name, from_number, args,
    )

    output: Any
    try:
        if tool_name == "get_caller_context":
            output = _voice_caller_context(from_number)

        elif tool_name == "register_family":
            output = await _register_family(
                sender_phone=from_number,
                parent_name=str(args.get("parent_name", "")).strip(),
                kid_name=str(args.get("kid_name", "")).strip(),
                kid_phone=normalize_phone(str(args.get("kid_phone", ""))),
            )

        elif tool_name == "wait_for_kid_confirmation":
            kid_phone = str(args.get("kid_phone", ""))
            timeout = int(args.get("timeout_seconds") or KID_VERIFICATION_TIMEOUT_SECONDS)
            output = await _voice_wait_for_kid_confirmation(kid_phone, timeout)

        elif tool_name == "check_d2l_grades":
            output = await _voice_check_d2l_grades(
                call_id=call_id, sender_phone=from_number, args=args
            )

        elif tool_name == "handoff_to_sms":
            body = str(args.get("body", "")).strip()
            if not body:
                output = {"ok": False, "error": "body required"}
            else:
                try:
                    await send_message(from_number, body)
                    output = {"ok": True}
                except Exception as e:
                    log.exception("handoff_to_sms send failed")
                    output = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        elif tool_name == "end_call":
            reason = str(args.get("reason", ""))
            await agentphone_end_call(call_id, reason)
            output = {"ok": True}

        else:
            output = {"error": f"unknown tool '{tool_name}'"}

    except Exception as e:
        log.exception("voice tool %s raised", tool_name)
        output = {"error": f"{type(e).__name__}: {e}"}

    # Normalize to string for the tool_result. Some voice runtimes prefer
    # strings; nested objects are JSON-encoded.
    output_str = output if isinstance(output, str) else json.dumps(output)

    return {"tool_call_id": tool_call_id, "output": output_str}
