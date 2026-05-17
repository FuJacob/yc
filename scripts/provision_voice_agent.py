"""Create the Kiddio voice agent on AgentPhone and bind it to the existing number.

Usage:
    .venv/bin/python scripts/provision_voice_agent.py --webhook-url https://<ngrok>/webhook/voice

Picks up AGENT_PHONE_API_KEY and AGENT_PHONE_NUMBER_ID from .env. Prints the new
voice agent_id — paste it into .env as VOICE_AGENT_ID.

Re-run after editing the system prompt or tool schemas to update the agent in place.
If the agent already exists at the same name, this script PATCHes instead of POSTing.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from dotenv import load_dotenv

from agent import VOICE_SYSTEM_PROMPT
from config import (
    AGENT_PHONE_API_KEY,
    AGENT_PHONE_BASE_URL,
    AGENT_PHONE_NUMBER_ID,
)
from tools import VOICE_TOOL_SCHEMAS

load_dotenv()

AGENT_NAME = "kiddio-voice"


def _headers() -> dict:
    if not AGENT_PHONE_API_KEY:
        sys.exit("AGENT_PHONE_API_KEY missing — fill .env first")
    return {
        "Authorization": f"Bearer {AGENT_PHONE_API_KEY}",
        "Content-Type": "application/json",
    }


def find_existing_agent(client: httpx.Client) -> dict | None:
    """Look up an existing agent named kiddio-voice. Returns the dict or None."""
    r = client.get(f"{AGENT_PHONE_BASE_URL}/v1/agents", headers=_headers())
    if r.status_code == 404:
        return None
    r.raise_for_status()
    body = r.json()
    agents = body.get("agents") or body.get("data") or body if isinstance(body, list) else body.get("agents", [])
    if isinstance(body, list):
        agents = body
    for a in agents or []:
        if a.get("name") == AGENT_NAME:
            return a
    return None


def create_or_update(webhook_url: str) -> str:
    payload = {
        "name": AGENT_NAME,
        "channel": "voice",
        "system_prompt": VOICE_SYSTEM_PROMPT,
        "tools": VOICE_TOOL_SCHEMAS,
        "interruptible": True,
        "max_call_seconds": 600,
        "webhook_url": webhook_url,
    }

    with httpx.Client(timeout=30) as client:
        existing = None
        try:
            existing = find_existing_agent(client)
        except Exception as e:
            print(f"[warn] couldn't list agents to check for existing kiddio-voice: {e}")

        if existing and existing.get("id"):
            agent_id = existing["id"]
            print(f"Found existing voice agent {agent_id} — updating…")
            r = client.patch(
                f"{AGENT_PHONE_BASE_URL}/v1/agents/{agent_id}",
                headers=_headers(),
                json=payload,
            )
            if r.status_code >= 400:
                # Some APIs don't support PATCH on agents — try PUT instead
                r = client.put(
                    f"{AGENT_PHONE_BASE_URL}/v1/agents/{agent_id}",
                    headers=_headers(),
                    json=payload,
                )
            r.raise_for_status()
            return agent_id

        print("Creating new voice agent…")
        r = client.post(
            f"{AGENT_PHONE_BASE_URL}/v1/agents",
            headers=_headers(),
            json=payload,
        )
        if r.status_code >= 400:
            print(f"[error] {r.status_code}: {r.text[:500]}", file=sys.stderr)
            r.raise_for_status()
        body = r.json()
        agent_id = (
            body.get("agent_id")
            or body.get("id")
            or body.get("data", {}).get("id")
        )
        if not agent_id:
            sys.exit(f"Agent created but no id in response: {body}")
        return agent_id


def bind_to_number(agent_id: str) -> None:
    if not AGENT_PHONE_NUMBER_ID:
        print("[warn] AGENT_PHONE_NUMBER_ID not set — skipping number binding")
        return
    print(f"Binding agent {agent_id} to number {AGENT_PHONE_NUMBER_ID} for channel=voice…")
    with httpx.Client(timeout=30) as client:
        r = client.post(
            f"{AGENT_PHONE_BASE_URL}/v1/numbers/{AGENT_PHONE_NUMBER_ID}/agents",
            headers=_headers(),
            json={"agent_id": agent_id, "channel": "voice"},
        )
        if r.status_code >= 400:
            print(f"[warn] binding returned {r.status_code}: {r.text[:300]}")
            print(
                "If the API path differs in your AgentPhone version, bind in the "
                "dashboard instead and add VOICE_AGENT_ID to .env."
            )
            return
        print("Bound.")


def main():
    ap = argparse.ArgumentParser(description="Provision the Kiddio voice agent on AgentPhone")
    ap.add_argument(
        "--webhook-url",
        required=True,
        help="Public URL of your /webhook/voice endpoint (e.g. https://abc123.ngrok.app/webhook/voice)",
    )
    ap.add_argument(
        "--skip-bind",
        action="store_true",
        help="Skip the number-binding step (do it in the dashboard yourself)",
    )
    args = ap.parse_args()

    agent_id = create_or_update(args.webhook_url)
    print(f"\n  VOICE_AGENT_ID={agent_id}\n")

    if not args.skip_bind:
        bind_to_number(agent_id)

    print(
        "\nNext steps:\n"
        f"  1. Add VOICE_AGENT_ID={agent_id} to your .env\n"
        "  2. Confirm the binding in the AgentPhone dashboard if step 1 warned\n"
        "  3. Restart the FastAPI server\n"
        "  4. Call the AgentPhone number — voice should answer\n"
    )


if __name__ == "__main__":
    main()
