# RFC: FamilyOps MVP

**Status:** Ready for Implementation
**Author:** Jacob Fu
**Date:** 2026-05-17
**Hackathon:** Call My Agent (AgentPhone @ YC, May 17 2026)
**Revision:** v3

---

## 1. Summary

FamilyOps is an iMessage agent that lets a parent text questions about their kid's school — starting with grades. MVP flow:

1. Parent texts the AgentPhone number with their name + kid's name + kid's phone
2. Bot texts the kid for verification, kid replies YES
3. Parent asks "what are Alex's grades?" → bot fires up local Chrome (already logged into D2L), Browser Use navigates the portal, returns grades, bot replies via iMessage
4. Bot also pings the kid: "FYI your parent just checked your grades."

This RFC is the contract for what gets built today. Anything not listed here is post-hackathon.

---

## 2. MVP Scope

### In Scope
- One AgentPhone number (iMessage primary, SMS works too)
- LLM-driven onboarding via tool calls (no hand-rolled state machine)
- Parent → kid verification text → YES confirmation
- Parent asks free-form grade questions → local Browser Use agent logs into `learn.uwaterloo.ca/d2l/` using the existing Chrome profile → returns grades
- Auto-notify kid after each grade query

### Out of Scope (Explicit)
- Voice calls
- Purchase / approval flow (designed for, not built)
- Email integration (AgentMail)
- Multiple kids per family
- Multiple guardians per family
- Multiple school portals
- 2FA handling — relies on existing Chrome session already past 2FA
- Web UI / dashboard
- Stripe / Sponge / Moss / Supermemory
- Cloud deploy — everything runs on the demo laptop with ngrok

---

## 3. Architecture

```
                  ┌──────────────────────┐
   Parent ──iMsg→ │   AgentPhone Number  │ ←─iMsg── Kid
                  └──────────┬───────────┘
                             │ webhook (agent.message)
                             ▼
                  ┌──────────────────────┐
                  │  FastAPI /webhook    │
                  │  - verify HMAC sig   │
                  │  - resolve sender    │
                  │  - 200 OK fast       │
                  │  - kick BG task      │
                  └──────────┬───────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │  Orchestrator LLM    │
                  │  gpt-5.4-nano        │ ← OpenAI SDK, tool calling
                  │  (decides tool to    │
                  │   call, writes reply)│
                  └──────────┬───────────┘
                             │
                ┌────────────┼──────────────┐
                ▼            ▼              ▼
        ┌────────────┐ ┌──────────┐ ┌──────────────┐
        │ browser_use│ │  SQLite  │ │  AgentPhone  │
        │  (LOCAL)   │ │ families │ │  send msg    │
        │ Agent +    │ │  users   │ │              │
        │ Chrome     │ │          │ │              │
        │ profile    │ │          │ │              │
        │            │ │          │ │              │
        │ LLM:       │ │          │ │              │
        │ ChatBrowser│ │          │ │              │
        │ Use →      │ │          │ │              │
        │ Sonnet 4.6 │ │          │ │              │
        └────────────┘ └──────────┘ └──────────────┘
```

One FastAPI process. Local during the hackathon, exposed to AgentPhone via ngrok. The browser_use agent runs Chrome **on the demo laptop** with `user_data_dir` pointing at the user's existing Chrome profile (already logged into D2L). The LLM driving the browser is Claude Sonnet 4.6, accessed via `ChatBrowserUse()` which proxies through Browser Use's hosted models using our `BROWSER_USE_API_KEY` (so no OpenAI cost for browser steps).

---

## 4. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Messaging | **AgentPhone** | Required. Webhook delivers iMessage + SMS. We POST `/v1/messages` to reply. |
| Runtime | **Python 3.12 + FastAPI** | Browser Use's OSS package is Python-first. Reagan's hackathon stack. |
| Orchestrator LLM | **`gpt-5.4-nano-2026-03-17`** via OpenAI SDK | Cheap, fast, tool-calling. Used for: routing inbound messages, calling tools, formatting iMessage replies. |
| Browser automation | **`browser-use`** Python package (OSS, local) — `Agent` + `BrowserSession` | Runs Chromium on the laptop, attached to the user's real Chrome profile dir so D2L is already logged in. No credentials in our code. |
| Browser LLM | **`ChatBrowserUse()`** → `claude-sonnet-4.6` via Browser Use's hosted models | Uses `BROWSER_USE_API_KEY` (we have credits). No OpenAI spend on the browser loop. Sonnet 4.6 is what Browser Use tunes for. |
| Database | **SQLite** (`familyops.db`) | One file, zero setup. Two tables. |
| Tunneling | **ngrok** | Static domain if available. |
| Secrets | `.env` + `python-dotenv` | Standard. |

### Rejected
- **Twilio** — AgentPhone covers both channels.
- **Browser Use cloud SDK (`browser_use_sdk`)** — runs Chromium in their cloud; would force us to either ship D2L credentials to their cloud or wrestle with their profile-sync flow. Local Chrome with the existing profile is simpler and more demo-friendly (judges see real Chrome opening live).
- **Vercel deploy** — no need.
- **Postgres / Neon** — one family, two rows.
- **Credential storage / Fernet** — we don't store credentials.
- **Supermemory, AgentMail, Stripe, Moss, Sponge** — none needed for MVP demo.

---

## 5. Data Model

Two tables. Deliberately thin.

### `families`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `created_at` | TEXT | ISO timestamp |

### `users`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `family_id` | INTEGER FK | |
| `phone` | TEXT UNIQUE | E.164, e.g. `+14155551234` |
| `name` | TEXT | First name |
| `role` | TEXT | `parent` or `kid` |
| `onboarding_state` | TEXT | `pending_verification`, `verified`. LLM tools mutate this. |
| `created_at` | TEXT | |

### What we are NOT storing
- **Grades, assignments, emails, anything scraped.** Always fetched live. Cache later if Browser Use latency hurts.
- **D2L credentials or session tokens.** They live in Chrome's profile dir, which we just point at — never read or write that data ourselves.
- **Conversation history.** AgentPhone gives us `recentHistory` in every webhook.

### Growth slots (NOT building today)
- `pending_actions` — purchase / signature approvals
- `events` — append-only audit log of agent actions
- `kid_settings` — budget caps, blocked categories

Existing field names (`role`, `onboarding_state`) accommodate these without rename.

---

## 6. LLM Tool Surface (orchestrator)

The orchestrator LLM gets a fixed tool list. On each inbound message we hand it the sender's record (or null), the family context, the recent message history, and the new message. It decides what to do.

| Tool | Purpose | Side effects |
|---|---|---|
| `register_family(parent_name, kid_name, kid_phone)` | Unknown number provided kid info. | Creates `families` + two `users` rows. Sends the verification iMessage to the kid. Returns ids. |
| `confirm_kid(kid_phone)` | `pending_verification` kid replied YES. | Flips both users to `verified`. Sends confirmation iMessage to the parent. |
| `check_d2l_grades(student_name)` | Verified parent asked about grades. | Calls the local `browser_use` agent; returns grade summary string. After the orchestrator's reply lands, a fire-and-forget "your parent checked your grades" goes to the kid (hardcoded, not an LLM tool). |

Replies to the user are written by the LLM itself — except the kid verification text and the parent confirmation, which are templated inside the tools so they're deterministic.

### Loop limits
- Max 4 tool calls per inbound message.
- 60-second hard timeout on `check_d2l_grades`.

---

## 7. Browser Use Integration

Pattern (lifted from a known-good codebase):

```python
from pathlib import Path
from browser_use import Agent, ChatBrowserUse, BrowserSession

chrome_profile = Path.home() / "Library/Application Support/Google/Chrome"

session = BrowserSession(
    user_data_dir=chrome_profile,
    headless=False,           # visible for the demo
    channel="chrome",         # real Chrome, not chromium
)

llm = ChatBrowserUse()        # Sonnet 4.6 via BROWSER_USE_API_KEY

agent = Agent(
    task=(
        "You are already logged into D2L. Go to learn.uwaterloo.ca/d2l/. "
        "Open the Grades page for the current term. Extract every course "
        "with its current grade. Return as plain text, one course per line, "
        "format: 'COURSE_CODE: GRADE'. Do not close the browser."
    ),
    llm=llm,
    browser_session=session,
    max_failures=3,
    use_vision=True,
    max_actions_per_step=2,
)

result = await agent.run(max_steps=25)
extracted = result.final_result()   # string we hand back to orchestrator
```

### Chrome caveat
`user_data_dir` requires Chrome to not be running on that profile when the agent launches (Chrome locks it). Two options:

- **Demo flow (recommended):** keep Chrome closed during agent runs. The judges see Browser Use's window open live — that's the visual.
- **Dev flow:** create a dedicated profile dir (e.g. `./chrome-profile`), launch Chrome once manually with `--user-data-dir=./chrome-profile`, log into D2L, close. Subsequent agent runs use that dir without fighting the user's main Chrome.

Phase 0 should pick one and stick with it. **Recommendation: dedicated `./chrome-profile` dir** — leaves the user's daily-driver Chrome alone, removes "did I remember to close Chrome?" as a demo failure mode.

---

## 8. User Flows

### 8.1 Onboarding (~30s in the demo)

```
Parent → bot:   "Hey, I'm Jacob, register my kid Alex at +14155551234"

                LLM: unknown sender. Extracts name="Jacob", kid_name="Alex",
                kid_phone="+14155551234". Calls register_family(...).
                Tool creates rows + sends verification iMessage to kid.

bot → Parent:   "Got it Jacob — texting Alex now to confirm."

bot → Kid:      "Hi Alex, your parent Jacob just registered you with
                 FamilyOps so they can help with school stuff like
                 checking grades. Reply YES to confirm this is you."

Kid → bot:      "yes"

                LLM: kid in pending_verification. Calls confirm_kid(phone).
                Tool flips state + iMessages parent.

bot → Kid:      "Thanks Alex, you're all set."
bot → Parent:   "Alex is verified. Try asking 'what are Alex's grades?'"
```

LLM normalizes whatever phone format the parent uses (`415-555-1234`, etc.) to E.164.

### 8.2 Grade Query

```
Parent → bot:   "what are Alex's grades?"

                LLM: verified parent, kid=Alex. Calls check_d2l_grades("Alex").
                Tool spawns the browser_use Agent (see §7).
                Latency: ~20-40s.
                If >5s elapsed without result, bot sends "Checking now…"
                Tool returns extracted grades.
                LLM formats iMessage-friendly reply.

bot → Parent:   "Alex's current grades:
                 CS 246: 87%
                 MATH 239: 92%
                 STAT 230: 78%
                 ENGL 109: 85%
                 Lowest: STAT 230 at 78%."

                After parent reply is sent, hardcoded fire-and-forget:

bot → Kid:      "FYI Jacob just checked your grades."
```

The kid notification is **not** an LLM tool. Hardcoded after `check_d2l_grades` succeeds so it can't be skipped.

### 8.3 Edge cases (graceful fallbacks)
- Unknown number texts before registering → LLM replies with the registration prompt.
- Kid texts something other than YES post-verification → bot says "Only your parent uses me right now."

---

## 9. File Layout

```
familyops/
  main.py                 # FastAPI app, /webhook handler
  agent.py                # Orchestrator: build context, run LLM loop
  tools.py                # Tool schema + dispatcher
  agentphone_client.py    # send_message, verify_signature
  browser_agent.py        # browser_use Agent wrapper for check_d2l_grades
  db.py                   # SQLite connection, queries
  config.py               # env var loading, constants
  requirements.txt
  .env.example
  README.md               # setup + demo runbook
```

Plain functions per module. No premature classes.

---

## 10. Implementation Phases

Time-boxed against 9:30 AM → 8 PM (10.5 hrs of hacking).

### Phase 0 — Setup (30 min)
- AgentPhone account, get number + webhook secret + API key
- Create `./chrome-profile` dir; launch Chrome manually with `--user-data-dir=./chrome-profile`; log into D2L; close Chrome
- `.env` filled (AgentPhone, OpenAI, Browser Use keys)
- `pip install -r requirements.txt`; `playwright install chromium` if needed
- ngrok running, webhook URL set in AgentPhone dashboard

### Phase 1 — Echo loop (1 hr)
- FastAPI `/webhook` receives, verifies HMAC, parses payload
- Sends a hardcoded echo reply via `agentphone_client.send_message`
- **Done when:** texting the AgentPhone number returns "echo: <your message>".

### Phase 2 — DB + sender resolution (1 hr)
- SQLite schema, helpers (`get_user_by_phone`, `create_family`, `create_user`, `set_state`, `get_kid_for_parent`)
- On every inbound, look up sender, attach to context object
- **Done when:** webhook logs include resolved sender + family context.

### Phase 3 — Orchestrator LLM with onboarding tools (2 hrs)
- OpenAI client (`gpt-5.4-nano-2026-03-17`)
- Tool definitions: `register_family`, `confirm_kid`
- Tool dispatcher + state mutations
- LLM loop with max-4 iterations
- **Done when:** parent → registration → kid YES → both confirmed works end-to-end.

### Phase 4 — Browser Use grade check (2 hrs)
- `browser_agent.py`: `Agent + BrowserSession + ChatBrowserUse` pattern from §7
- Wrap into `check_d2l_grades` tool
- Wire into orchestrator
- Interstitial "Checking now…" if browser task >5s (timer-based early send)
- Hardcoded kid notification on success
- **Done when:** "what are Alex's grades?" returns real D2L data and kid gets the heads-up.

### Phase 5 — Polish + demo prep (2 hrs)
- Error handling: D2L slow, Browser Use error, LLM tool errors → graceful texts
- README with exact demo runbook
- Practice run end-to-end, time it
- Test iMessage delivery (blue bubbles) to demo phones

### Phase 6 — Slack (2 hrs)
- Buffer. Things will break.

---

## 11. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| D2L UI changes break Browser Use mid-demo | Test the exact grade-check task ≥2 times during Phase 4. If shaky, cache last successful output as canned fallback. |
| D2L session in `./chrome-profile` expires | Re-log-in 30 min before demo. Test the agent right after. |
| Chrome profile lock conflict | We use a dedicated profile dir, not the main one. Don't double-launch the agent. |
| AgentPhone webhook delivery delays/loss | ngrok logs visible; have a manual `send_message` CLI ready as backup. |
| LLM hallucinates a kid name not in DB | Tool returns error; LLM apologizes. |
| iMessage doesn't deliver | SMS fallback works on the same number. |
| Browser Use rate-limit / slow | Hard 60s timeout + interstitial text covers UX. |
| `gpt-5.4-nano` too dumb for tool routing | Swap to `gpt-5.4-mini` or `gpt-5`. Same key, one-line change. |
| Local Chromium dies / crashes | Agent retries `max_failures=3`. If still failing, return graceful error. |

---

## 12. Demo Script (90s)

**0:00 — Hook (10s).** "Parents juggle six school portals. We built one phone number that does it for them."

**0:10 — Live onboarding (20s).** Texts on screen:
- Parent: "Hey, I'm Jacob, register my kid Alex at +1…"
- Bot: "Got it Jacob, texting Alex now."
- Kid: "yes"
- Bot: "Alex is verified."

**0:30 — Live grade query (40s).**
- Parent: "what are Alex's grades?"
- Browser Use Chrome window opens live, navigates D2L
- iMessage reply with real grades arrives
- Kid's phone shows the auto-notification text

**1:10 — What's next (20s).** "Today: grades. Same architecture handles permission slips, kid purchase approvals, school emails. Anywhere parents waste time, this agent goes."

---

## 13. Open Questions

None. Implementation can start on go.
