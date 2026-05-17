import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


# AgentPhone (tolerate both AGENT_PHONE_* and AGENTPHONE_* naming)
AGENT_PHONE_API_KEY = _env("AGENT_PHONE_API_KEY", "AGENTPHONE_API_KEY")
AGENT_PHONE_AGENT_ID = _env("AGENT_PHONE_AGENT_ID", "AGENTPHONE_AGENT_ID")
AGENT_PHONE_NUMBER_ID = _env("AGENT_PHONE_NUMBER_ID", "AGENTPHONE_NUMBER_ID")
AGENT_PHONE_WEBHOOK_SECRET = _env(
    "AGENT_PHONE_WEBHOOK_SECRET", "AGENTPHONE_WEBHOOK_SECRET"
)
AGENT_PHONE_BASE_URL = "https://api.agentphone.ai"

# OpenAI (orchestrator)
OPENAI_API_KEY = _env("OPENAI_API_KEY")
ORCHESTRATOR_MODEL = _env(
    "ORCHESTRATOR_MODEL", default="gpt-5.4-nano-2026-03-17"
)

# Browser Use
BROWSER_USE_API_KEY = _env("BROWSER_USE_API_KEY")

# Supermemory (semantic family memory — see rfc-7.md)
SUPERMEMORY_API_KEY = _env("SUPERMEMORY_API_KEY")
SUPERMEMORY_BASE_URL = "https://api.supermemory.ai"

# Voice (RFC-3)
VOICE_AGENT_ID = _env("VOICE_AGENT_ID")
KID_VERIFICATION_TIMEOUT_SECONDS = int(_env("KID_VERIFICATION_TIMEOUT_SECONDS", default="45"))
BROWSER_USE_FETCH_TIMEOUT_SECONDS = int(_env("BROWSER_USE_FETCH_TIMEOUT_SECONDS", default="90"))
VOICE_NARRATION_INTERVAL_SECONDS = float(_env("VOICE_NARRATION_INTERVAL_SECONDS", default="4.5"))

# Sponge (parent's wallet — RFC-4)
SPONGE_API_KEY = _env("SPONGE_API_KEY")
SPONGE_API_URL = _env("SPONGE_API_URL")

# Payment policy
PAYMENT_REQUEST_TTL_MINUTES = int(_env("PAYMENT_REQUEST_TTL_MINUTES", default="30"))
PAYMENT_DEFAULT_CHAIN = _env("PAYMENT_DEFAULT_CHAIN", default="base")

# Paths
PROJECT_ROOT = Path(__file__).parent.resolve()
CHROME_PROFILE_DIR = PROJECT_ROOT / "chrome-profile"
DB_PATH = PROJECT_ROOT / "familyops.db"

# D2L
D2L_URL = "https://learn.uwaterloo.ca/d2l/"

# Limits
MAX_TOOL_CALLS = 6  # bumped from 4 in rfc-7 — now 5 LLM tools (incl. remember_fact, recall)
BROWSER_TIMEOUT_SECONDS = 90
MEMORY_RECALL_LIMIT = 3
MEMORY_TIMEOUT_SECONDS = 5
