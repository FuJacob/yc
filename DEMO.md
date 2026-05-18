# Riley — 3-Minute Demo Script

**Format:** Pre-recorded video for judge submission.
**Target:** 3 minutes flat. Tight, no filler.

---

## Setup Before Recording

1. Run `./go.sh` (resets DB, restarts server)
2. Have two phones ready: parent phone + kid phone
3. Screen-record the parent phone (iPhone Mirroring or screen share)
4. Have the browser live view ready to show on laptop
5. AgentPhone number: `+1 (412) 654-3597`

---

## The Script

### [0:00–0:25] THE PROBLEM (voiceover, show phones/school portals)

> "If you're a parent, your kid's school life is scattered across six different portals, dozens of logins, and a phone that never stops buzzing. You want to know one simple thing — how's my kid doing? But you need to log into D2L, navigate three menus, remember which courses they're in, and somehow make sense of a gradebook designed for professors, not parents.

> And when your kid texts asking for money for some app — you can't even verify if their grades are up to date before saying yes.

> We built Riley. One phone number that handles all of it."

### [0:25–0:55] ONBOARDING (show parent phone live)

> "Here's how it works. I text Riley from my phone."

**Parent sends:**
> hey, i'm jacob. my kid is gaby and her number is [kid's number]

**Show Riley's reply** — it asks Gaby to confirm.

**Switch to kid phone — Gaby gets a text:**
> "Hey Gaby, your parent Jacob just set you up with Riley..."

**Kid replies:** "yeah that's me"

**Show both phones getting confirmation.**

> "That's it. Two texts, both sides verified, no app download, no account creation. Riley works over iMessage — the thing every family already uses."

### [0:55–1:55] GRADE CHECK (the big moment — show parent phone + live browser)

> "Now the real magic. I ask Riley about Gaby's grades."

**Parent sends:**
> how are gaby's grades looking?

**Show the reply:** "checking now..." + live view link

> "Riley just spun up a cloud browser through Browser Use. It's logging into the University of Waterloo's D2L portal right now — the real one, with real credentials, real 2FA."

**Show the live browser view on laptop** — the browser navigating D2L, clicking into grades.

> "The parent gets a live link to watch this happening in real time. No black box — you see exactly what Riley sees."

**Show the final grade summary arriving on parent phone.**

> "Riley reads the gradebook and sends back a parent-friendly summary. Not a raw data dump — it tells you what matters. Which assignments are done, what's missing, and what to focus on next."

### [1:55–2:30] PAYMENT + MESSAGING (show kid phone → parent phone)

> "Now here's where it all connects. Gaby texts Riley asking to pay for something."

**Kid sends:**
> can you pay $2 for chegg?

**Show parent phone getting the request:**
> "gaby wants $2.00 for chegg. want to go ahead?"

> "The parent sees the request in the same conversation. No separate app, no notification to hunt for."

**Parent replies:** "yeah go ahead"

> "Funds move instantly through Sponge Wallet on Solana. And because Riley just checked Gaby's grades, I know whether she's earned it."

**Parent sends:**
> actually, tell gaby to finish her mobius assignments first

**Show kid phone receiving the message from Riley.**

> "Riley can relay messages between parent and kid — the whole family runs through one agent."

### [2:30–2:55] TECH + ARCHITECTURE (voiceover, show architecture diagram or README)

> "Under the hood: AgentPhone handles all iMessage and SMS communication — it's the backbone that makes Riley reachable on every phone. Browser Use gives us cloud browsers with persistent profiles and live streaming, so we can navigate real school portals with real logins. Sponge moves real money on Solana when a parent approves a payment. Supermemory stores family context across conversations — grades, preferences, school info — so Riley gets smarter over time. And the orchestrator is an OpenAI tool-calling loop that ties it all together."

### [2:55–3:00] CLOSE

> "Six portals, one text. Riley — one AI agent for the whole family."

---

## Key Points to Hit

These are what judges care about. Make sure every one lands:

- **Real problem, real users.** Parents actually deal with this. It's not hypothetical.
- **Works on iMessage.** No app download. No signup flow. Text a number and go.
- **Live browser, not a mock.** The D2L login and grade scraping is real — show the live view.
- **Real money moves.** Sponge sends actual funds on Solana. Not a simulation.
- **Every sponsor tech is load-bearing.** AgentPhone (communication), Browser Use (grade checking), Sponge (payments), Supermemory (memory), OpenAI (orchestration). None are checkbox integrations.
- **Multi-channel agent.** Voice, SMS, iMessage, browser, payments — all through one number.
- **Two-sided.** Both parent and kid interact with the same agent. It's not a single-user tool.

---

## Backup Plan

If anything breaks during recording:

- **AgentPhone down:** Use a screen recording from a successful test run. Splice it in.
- **D2L session expired:** Re-run `./go.sh` and re-login via Browser Use profile. Or use backup footage of a working grade check.
- **Browser Use timeout:** Increase `BROWSER_TIMEOUT_SECONDS=240` in `.env` and retry.
- **Payment fails:** Check `scripts/sponge-status.py` for wallet balance. Use backup footage if needed.

Record multiple takes. Use the cleanest one. Judges won't know it's take 4.

---

## Pre-Recording Checklist

```
[ ] ./go.sh ran clean
[ ] Parent phone can text AgentPhone number (blue bubbles)
[ ] Kid phone can text AgentPhone number (blue bubbles)
[ ] Test onboarding flow end-to-end
[ ] Test grade check — browser opens, grades return
[ ] Test payment request — kid asks, parent approves, funds move
[ ] Test send_message_to_kid — parent tells Riley to text the kid
[ ] Screen recording software ready (parent phone + laptop for live view)
[ ] Backup footage saved from test runs
```
