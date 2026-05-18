# Riley

One AI agent for the whole family — reachable by iMessage/SMS.

Riley checks your kid's grades on UWaterloo D2L, handles kid-initiated payment requests via Sponge Wallet, and remembers family context across conversations. Built at the Call My Agent Hackathon @ YC, 2026-05-17.

---

## What it does

1. **Onboarding** — Parent texts Riley with their name, kid's name, and kid's phone. Riley texts the kid to verify.
2. **Grade checking** — Parent asks "how are Gaby's grades?" and Riley opens a cloud browser, logs into D2L, reads the gradebook, and sends back a parent-friendly summary with a live view link.
3. **Payment requests** — Kid texts "can you pay $2 for Chegg?" and Riley asks the parent for approval. On approval, funds move via Sponge Wallet.
4. **Memory** — Riley remembers facts about your family across conversations (school info, preferences, schedules) via Supermemory.
5. **General assistant** — Off-topic questions (homework help, recipes, trivia) get answered naturally.

---

## Architecture

```
iMessage/SMS  <-->  AgentPhone  <-->  FastAPI webhook  <-->  OpenAI orchestrator
                                           |
                            +--------------+--------------+
                            |              |              |
                      Browser Use    Sponge Wallet   Supermemory
                      (D2L grades)   (payments)      (family memory)
```

- **Orchestrator**: OpenAI tool-calling loop (`gpt-5.4-nano`)
- **Browser**: Browser Use Cloud SDK with live streaming
- **Payments**: Sponge Wallet SDK (Solana/Base)
- **Memory**: Supermemory semantic search
- **DB**: SQLite (families, users, payment requests, audit trail)
- **Frontend**: Static landing page

---

## Setup

### 1. Install deps

```bash
pip install -r requirements.txt
```

### 2. Configure `.env`

```
OPENAI_API_KEY=sk-...
BROWSER_USE_API_KEY=bu_...
BROWSER_USE_PROFILE_ID=...
PUBLIC_URL=https://your-ngrok-url.ngrok-free.dev
AGENT_PHONE_API_KEY=sk_live_...
AGENT_PHONE_AGENT_ID=...
AGENT_PHONE_NUMBER_ID=...
SUPERMEMORY_API_KEY=sm_...
D2L_USERNAME=user@uwaterloo.ca
D2L_PASSWORD=...
SPONGE_API_KEY=sponge_live_...
KID_DEFAULT_PAYOUT_DESTINATION=...
PAYMENT_REQUEST_TTL_MINUTES=30
PAYMENT_DEFAULT_CHAIN=solana
```

### 3. Sync browser profile (for D2L cookies)

```bash
curl -fsSL https://browser-use.com/profile.sh | sh
```

This uploads your local Chrome cookies to the Browser Use cloud profile. D2L sessions expire in ~20-30 min, so re-run before demos.

### 4. Start ngrok

```bash
ngrok http 8000
```

Copy the public URL into `PUBLIC_URL` in `.env`.

### 5. Run

```bash
uvicorn main:app --reload
```

Or use the full reset + restart script:

```bash
bash go.sh
```

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/start.sh` | Boot FastAPI + ngrok, register webhook |
| `scripts/stop.sh` | Kill both processes |
| `scripts/restart.sh` | Stop + start |
| `scripts/status.sh` | Process state, tunnel URL, DB summary |
| `scripts/logs.sh` | Tail server log (`-t` adds tunnel log) |
| `scripts/reset-db.sh` | Wipe DB + Supermemory, recreate schema |
| `scripts/sponge-status.py` | Smoke test Sponge wallet balances |

---

## Demo flow

### Onboarding

Parent texts the agent number:

> Hi, I'm Jacob, my kid is Gaby and her number is 555-123-4567

Riley registers the family and texts Gaby to confirm. Gaby replies "yes" and both sides are verified.

### Grade check

Parent texts:

> how are Gaby's grades looking?

Riley opens a cloud browser, logs into D2L, reads the MATH 235 gradebook, and sends a parent-friendly summary. The parent also gets a live view link to watch the browser in real time.

### Payment request

Kid texts:

> can you pay $2 for Chegg?

Parent receives: "Gaby wants $2.00 for Chegg. Want to go ahead?"

Parent replies "yes" and funds are sent via Sponge Wallet.

---

## File map

| File | Purpose |
|---|---|
| `main.py` | FastAPI app, webhook handler, live view route, per-phone message queue |
| `agent.py` | OpenAI tool-calling orchestrator, system prompt, onboarding state machine |
| `tools.py` | Tool schemas + dispatcher for all agent actions |
| `browser_agent.py` | Browser Use Cloud SDK — creates sessions, streams steps, extracts grades |
| `payment_service.py` | Payment request state machine (kid request -> parent approval -> Sponge transfer) |
| `db.py` | SQLite schema + helpers (families, users, payments, onboarding sessions) |
| `config.py` | Environment variable loading, paths, constants |
| `agentphone_client.py` | AgentPhone API wrapper (send messages, verify webhook signatures) |
| `sponge_client.py` | Sponge Wallet SDK wrapper (send funds, validate destinations) |
| `memory.py` | Supermemory wrapper (store/recall family facts) |
| `frontend/` | Static landing page (Riley branding) |

---

## Troubleshooting

- **D2L not logged in**: Re-run the profile sync script. Sessions expire in ~20-30 min.
- **AgentPhone 502**: Their servers go down. Check status or restart later.
- **Browser Use timeout**: Increase `BROWSER_TIMEOUT_SECONDS` in `.env` (default 180s).
- **Payment fails**: Run `scripts/sponge-status.py` to check wallet balance and API key.
- **History leak after DB reset**: Handled automatically — old messages are filtered by registration timestamp.
