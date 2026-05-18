# Riley — 3-Minute Demo Script

**Format:** Pre-recorded video for judge submission.
**Target:** 3 minutes. Every second counts.

---

## Setup Before Recording

1. Run `./go.sh` (resets DB, restarts server)
2. Two phones ready: parent phone + kid phone
3. Screen-record parent phone (iPhone Mirroring or screen share)
4. Laptop showing live browser view
5. AgentPhone number: `+1 (412) 654-3597`

---

## The Script

### [0:00–0:20] THE HOOK

> "Your kid has a phone. They have school portals, browsing history, a calendar, and they're asking you for money. You're managing all of that across six apps, a dozen logins, and a group chat that never stops.

> We built one phone number that replaces all of it. No app. No login. Just text."

### [0:20–0:45] ONBOARDING — "just text hi"

> "Watch. I text Riley from my phone."

**Parent sends:**
> hey, i'm jacob. my kid is gaby and her number is [kid's number]

**Show Riley's reply** — registers the family, texts Gaby to verify.

**Switch to kid phone — Gaby gets:**
> "hey gaby, your parent jacob just set you up with riley..."

**Kid replies:** "yeah that's me"

**Show both phones confirmed.**

> "Two texts. Both sides verified. That's onboarding. Every message runs through AgentPhone, our orchestrator decides what to do via OpenAI tool calling, and AgentPhone sends the reply. No routing logic, no intent classifier. The LLM handles all of it."

### [0:45–1:30] GRADES — "how are gaby's grades?"

**Parent sends:**
> how are gaby's grades looking?

**Show:** "checking now..." arrives, plus a live view link.

> "Riley just spun up a cloud browser through Browser Use. It's logging into the University of Waterloo's D2L portal right now. Real school, real credentials, real two-factor auth."

**Show the live browser on laptop** — navigating D2L, clicking into grades.

> "The parent gets a live streaming link to watch this happen. You see exactly what Riley sees. No black box."

**Show the grade summary arriving on parent phone.**

> "Riley reads the gradebook and sends a parent-friendly summary. Not a data dump. It tells you what's on track, what's missing, and what to do next. Browser Use gives us isolated sessions with persistent profiles, so one login carries across every grade check."

**Meanwhile, kid's phone shows:** "heads up, jacob is checking your grades"

> "And the kid always gets notified first. Transparency by design."

### [1:30–1:50] BROWSER HISTORY — "what has gaby been browsing?"

**Parent sends:**
> what has gaby been browsing?

**Kid's phone lights up:** "heads up, jacob is looking at your browser history"

**Show the browsing report arriving:** D2L study sessions, YouTube, Discord... and one adult content site flagged.

> "Riley highlights the good stuff, flags the concerning stuff calmly, and the kid knows it's happening. Every parent action notifies the kid before the parent sees results."

### [1:50–2:05] CALENDAR — "put dentist on her calendar"

**Parent sends:**
> put a dentist appointment on gaby's calendar for thursday at 3pm

**Kid's phone:** "jacob added dentist appointment to your calendar on thursday at 3pm"

> "The LLM extracts the event details from natural language and the kid gets the notification immediately. No Google Calendar invite to ignore."

### [2:05–2:35] PAYMENTS — "can you pay $2 for chegg?"

> "Now it's the kid's turn."

**Kid sends:**
> can you pay $2 for chegg?

**Show parent phone:** "gaby wants $2.00 for chegg. want to go ahead?"

**Parent replies:** "yeah go ahead"

> "Real money just moved. Sponge Wallet transfers funds on Solana, from parent wallet to kid's account. The kid texts a request, the parent approves conversationally, no keyword commands, just 'yeah'. Real blockchain, zero friction."

**Parent sends:**
> tell gaby to finish her mobius assignments before asking for more

**Kid's phone receives the message.**

> "And Riley relays messages both ways. Parent to kid, kid to parent. One number, full family communication."

### [2:35–2:55] THE STACK

> "Six capabilities from one phone number. AgentPhone is the backbone for all iMessage and SMS. Browser Use navigates real school portals with live streaming. Sponge moves real money on Solana. Supermemory stores family context across conversations so Riley gets smarter over time. And OpenAI's tool-calling loop orchestrates everything from a single system prompt."

### [2:55–3:00] CLOSE

> "Your kid has a phone. Now they have an agent. Riley."

---

## Key Points to Hit

- **Real problem.** Every parent deals with this. Six portals, scattered info, no single view.
- **No app.** Works on iMessage. Text a number and you're done.
- **Live browser.** D2L login and grade scraping is real. Show the live view.
- **Real money.** Sponge sends actual funds on Solana. Not a simulation.
- **Kid gets notified first.** Every parent action (grades, browsing, calendar) notifies the kid before the parent sees results. Transparency.
- **Every sponsor is load-bearing.** AgentPhone (comms), Browser Use (grades), Sponge (payments), Supermemory (memory), OpenAI (orchestration).
- **Two-sided.** Both parent and kid use the same number. It's a family agent, not a parent tool.

---

## Backup Plan

- **AgentPhone down:** Splice in footage from a successful test run.
- **D2L timeout:** Re-run `./go.sh`, retry. Or use backup footage.
- **Payment fails:** Check `scripts/sponge-status.py`. Use backup footage.

Record multiple takes. Use the cleanest one.

---

## Pre-Recording Checklist

```
[ ] ./go.sh ran clean
[ ] Parent phone texts AgentPhone number (blue bubbles)
[ ] Kid phone texts AgentPhone number (blue bubbles)
[ ] Test full flow: onboard → grades → browsing → calendar → payment → messaging
[ ] Screen recording ready (parent phone + laptop for live view)
[ ] Backup footage saved from test runs
```
