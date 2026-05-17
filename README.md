# FamilyOps

iMessage agent that lets a parent check their kid's grades by texting. Hackathon MVP for Call My Agent @ YC, 2026-05-17.

See [RFC.md](RFC.md) for design + scope.

---

## Prerequisites

- Python 3.11 (Homebrew: `brew install python@3.11`)
- Google Chrome installed at the standard location
- An AgentPhone account with API key + agent + number provisioned
- OpenAI API key
- Browser Use API key (for Claude Sonnet 4.6 via their hosted model proxy)
- `ngrok` (or any tunneling tool) to expose the local FastAPI to AgentPhone

---

## One-time setup

### 1. Python deps

```bash
/opt/homebrew/bin/python3.11 -m venv .venv   # must be 3.11+; 3.9 ships with macOS
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

### 2. Fill `.env`

Copy `.env.example` to `.env` and fill in:

```
AGENT_PHONE_API_KEY=sk_live_...
AGENT_PHONE_AGENT_ID=agt_...
AGENT_PHONE_NUMBER_ID=num_...
AGENT_PHONE_WEBHOOK_SECRET=          # leave empty for dev; signature check is skipped
OPENAI_API_KEY=sk-...
BROWSER_USE_API_KEY=bu_...
```

If you don't yet have `AGENT_PHONE_AGENT_ID` / `NUMBER_ID`, sign up via the API:

```bash
curl -X POST https://api.agentphone.ai/v0/agent/sign-up \
  -H "Content-Type: application/json" \
  -d '{"human_email":"you@example.com"}'

# then with the OTP from email:
curl -X POST https://api.agentphone.ai/v0/agent/verify \
  -H "Content-Type: application/json" \
  -d '{"verification_id":"ver_...","otp_code":"123456"}'
```

The verify response contains `agent_id`, `number_id`, `phone_number`, and `api_key`. Drop them into `.env`.

### 3. Prime the dedicated Chrome profile with D2L

We use a project-local Chrome profile dir so it doesn't fight your normal browser.

```bash
mkdir -p chrome-profile
open -na "Google Chrome" --args --user-data-dir="$PWD/chrome-profile"
```

In the Chrome window that opens:
1. Navigate to https://learn.uwaterloo.ca/d2l/
2. Log in (complete any 2FA / Duo push)
3. Confirm you can see the homepage
4. **Quit Chrome completely** (Cmd+Q) before running the agent — Chrome locks the profile dir.

### 4. ngrok auth (one-time)

```bash
# Get a token from https://dashboard.ngrok.com/get-started/your-authtoken
ngrok config add-authtoken YOUR_NGROK_TOKEN
```

### 5. Run everything

```bash
scripts/start.sh
```

This boots uvicorn, starts ngrok, reads the public URL from ngrok's local API, and POSTs that URL to AgentPhone as the agent webhook — in one shot. Output looks like:

```
ready.
  server pid:  12345
  tunnel pid:  12346
  tunnel url:  https://populate-stem-goggles.ngrok-free.dev
  webhook:     https://populate-stem-goggles.ngrok-free.dev/webhook
```

Other dev commands:

| Script | Does |
|---|---|
| `scripts/stop.sh` | Stops both processes |
| `scripts/restart.sh` | Stop + start (re-registers webhook with new ngrok URL) |
| `scripts/status.sh` | Process state + tunnel URL + DB summary + AgentPhone webhook config |
| `scripts/logs.sh` | `tail -F` server log (`-t` adds tunnel log) |
| `scripts/reset-db.sh` | Wipe `familyops.db`, recreate empty schema |
| `scripts/resend-verification.sh [phone]` | Re-fire verification text on AgentPhone 502 outage |

### Webhook secret (optional, for production)

The first call to AgentPhone's webhook-register endpoint returns a `secret`. If you set `AGENT_PHONE_WEBHOOK_SECRET=<that secret>` in `.env` and restart, the server starts verifying HMAC signatures on inbound webhooks. Leave it empty for dev — signature verification is skipped.

---

## Health check

```bash
curl localhost:8000/health      # local
curl $(cat /tmp/familyops-tunnel-url)/health  # via ngrok
```

---

## Demo

### Onboarding (one-time per family)

From the parent phone, text the AgentPhone number something like:

> Hey, I'm Jacob, register my kid Alex at +14155551234

Expected reply: `Got it Jacob — texting Alex now.`

The kid's phone receives:

> Hi Alex, your parent Jacob just registered you with FamilyOps...

Kid replies `YES`. They get a thanks; parent gets `Alex is verified.`

### Grade query

From the parent phone:

> what are Alex's grades?

You'll see Chrome open on the demo laptop, Browser Use navigates D2L, then the parent receives a grade summary. The kid simultaneously receives `FYI Jacob just checked your grades.`

---

## Daily reset

Wipe the SQLite db to start fresh:

```bash
rm familyops.db
```

(The next startup recreates the schema automatically.)

---

## Troubleshooting

- **Browser Use can't open Chrome:** make sure no Chrome window is open against `./chrome-profile`. Quit Chrome with Cmd+Q.
- **D2L logs out:** re-launch Chrome with `--user-data-dir=$PWD/chrome-profile`, log back in, quit Chrome.
- **Webhook 401:** either remove `AGENT_PHONE_WEBHOOK_SECRET` from `.env` (dev mode) or make sure it matches what AgentPhone returned.
- **OpenAI model 404:** `ORCHESTRATOR_MODEL` env var overrides the default. Try `gpt-5.4-mini` or `gpt-5` if `gpt-5.4-nano` isn't available on your account.
- **AgentPhone send_message 4xx:** check `AGENT_PHONE_AGENT_ID` is correct and the number is attached to the agent.

---

## File map

| File | What it does |
|---|---|
| `main.py` | FastAPI app + `/webhook` handler. Verifies HMAC, parses payload, kicks BG task. |
| `agent.py` | Orchestrator LLM loop. Builds context, runs OpenAI tool-call loop. |
| `tools.py` | Tool schemas + dispatcher. `register_family`, `confirm_kid`, `check_d2l_grades`. |
| `browser_agent.py` | Local `browser_use` Agent + `BrowserSession` + `ChatBrowserUse` for D2L. |
| `agentphone_client.py` | `send_message` + HMAC signature verification. |
| `db.py` | SQLite — `families`, `users`. Helpers for sender resolution. |
| `config.py` | Env var loading, paths, constants. |
