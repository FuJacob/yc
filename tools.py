import logging
from typing import Any

from agentphone_client import send_message
from browser_agent import check_d2l_grades
from db import (
    create_family_with_users,
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
