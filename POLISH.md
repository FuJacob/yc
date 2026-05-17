# Polish & Stretch Goals — Hackathon Edition

**Time left:** ~6 hrs of hacking (until 8 PM submission). Aim to ship Tier 1 fully before touching Tier 2.

Hackathons reward **visual wow + linear demo + clear pain** (per Reagan from Browser Use). Most polish here optimizes for *what the judge sees in 60 seconds*, not what's under the hood.

---

## Tier 1 — Must do (high impact, low risk, <90 min total)

These don't add features; they make the demo not break and not look amateur.

### 1.1 Pre-seed demo family (10 min)
Onboarding is the most fragile part of the demo (AgentPhone 502s, kid not replying YES on time, etc.). Pre-seed the DB with a verified Jacob + Gabe so the live demo opens on the grade query.
- Script: `scripts/seed-demo.sh` — inserts family + verified parent + verified kid with the demo phone numbers.
- Show onboarding as a 10-second slide or pre-recorded video clip, **then** run the grade query live.

### 1.2 Canned-grade fallback (15 min)
Browser Use against real D2L is the single biggest demo risk. Add a `DEMO_FALLBACK_GRADES` env var — if set, `check_d2l_grades` returns a hardcoded plausible summary instead of running the browser.
- Default: real browser runs.
- Flip the env var live if D2L is acting up. Judges never see the failure.

### 1.3 "Checking now…" interstitial that actually fires (15 min)
Today, the LLM is *supposed to* write an interstitial alongside the tool call but `gpt-5.4-nano` may not bother. Make this deterministic: as soon as `check_d2l_grades` is called, the tool synchronously sends a hardcoded `"Checking D2L for Gabe now — back in 30 seconds…"` text. Removes dead-air during the slow browser run.

### 1.4 Demo script + slide deck (30 min)
- One-sentence pitch, problem (parent inboxes screenshot), solution (one phone number), demo (live), close (what's next).
- Practice the run twice end-to-end with a stopwatch.

### 1.5 Visual element on screen during demo (20 min)
Judges can't see SMS bubbles from 30 feet away. Mirror the iMessage thread to a laptop:
- Either screen-share an iPhone (Mac → iPhone Mirroring) OR
- Build a tiny `/dashboard` route that polls SQLite + AgentPhone's conversations API and renders the conversation in big iMessage-style bubbles. ~50 lines of HTML.
- Stick the Browser Use Chrome window next to it. The visual of "agent literally logs into the portal while you watch" is the wow moment.

### 1.6 Backup demo recording (10 min)
Run the full flow once on the demo laptop with QuickTime screen-recording (phone screen + Chrome side-by-side). Worst case (AgentPhone down, ngrok down, D2L logged out), you play the recording.

---

## Tier 2 — Sponsor-track integrations (extra prizes)

Each of these is a *separate prize pool*. Most cost ~1–2 hrs and bolt onto the existing tool surface.

### 2.1 Supermemory (highest ROI — ~1 hr) 🎧 Sony WH-1000XM5
Family memory is the most natural fit. Use it to:
- Remember which portal each kid uses (D2L vs Canvas vs Brightspace)
- Remember "Gabe is in 10th grade, takes AP Calc, has a tutor on Tuesdays"
- Recall past grade snapshots so "did math drop since last week?" works
- Personal: agent remembers each parent's name + tone preferences

Add a `remember(key, value)` and `recall(query)` tool to the orchestrator. Story for the judges: *"The agent gets smarter about your family every conversation."*

### 2.2 Stripe (high-impact demo moment — ~2 hrs) 💰 $3K credits
The purchase-approval flow is the *killer demo* of this whole project. From kid's phone:
- Kid: "can I buy a TI-84 for math?"
- Agent finds the item (Browser Use against Amazon or a mock store)
- Agent → parent: "Gabe wants TI-84 Plus CE, $118.99. Approve? Reply YES."
- Parent: "yes"
- Agent generates a Stripe Checkout link, sends to parent
- Parent pays → agent confirms to both

You don't need to actually process the order. A Stripe Checkout link + success page is enough. Demo lands because:
- Multi-user coordination (kid ↔ agent ↔ parent)
- Real money (judges feel it)
- Hits the sponsor track

### 2.3 AgentMail (~1.5 hrs) 📨 Founding eng interviews + $250 gift cards
School emails about permission slips are the second-most painful parent pain. Add:
- `check_school_email()` tool — pulls latest school-related emails via AgentMail
- "Tell me if there's anything I need to sign this week" → reads inbox, surfaces permission slips with deadlines
- Stretch: actually fill out the permission slip via Browser Use after parent approves

### 2.4 Sponge (~30 min if Stripe is already done) 💵 $500
Sponge is the *agent-economy* angle — autopay rails for agent-initiated purchases. If we've already done Stripe, plug in Sponge as the payment layer instead and you get both tracks. Read their docs at the venue.

### 2.5 Skip these for time
- **Moss** — requires voice, we're text-only. Skip.
- **Google DeepMind / Gemini** — we're committed to OpenAI + Sonnet via Browser Use. Swap LLMs would burn an hour for no demo benefit.

---

## Tier 3 — Killer demo features (only if Tier 1+2 done)

### 3.1 Multi-kid support (~30 min)
Currently 1 family = 1 kid. Schema already supports `family_id`. Extend `register_family` to accept multiple kids OR add `add_kid` tool. Demo: "What are *Alex's* grades and *Mia's* upcoming assignments?" — agent disambiguates kids by name.

### 3.2 Proactive morning briefing (~45 min)
Cron job (or a `/cron/morning` endpoint hit by AgentPhone's scheduling) sends each parent a daily summary at 7 AM: grades, upcoming due, missing assignments. Demo: text "subscribe to daily briefing" → at demo time, fake the cron firing on stage.

### 3.3 Group thread (~30 min — depends on iMessage UX)
AgentPhone supports group conversations. Parent + kid + agent in one thread. Lets the agent address them differently in the same conversation. Real chaos energy. Risky — may not behave on iMessage.

---

## Tier 4 — Tech polish (low visibility, only if everything else done)

These won't win you anything alone, but they keep the demo from breaking.

### 4.1 Webhook idempotency
AgentPhone's webhooks can retry. Track `X-Webhook-ID` in a tiny `webhook_deliveries` table and 200-OK duplicates without re-running the LLM. Otherwise you risk double-replies on flaky network.

### 4.2 Real conversation context
Right now we feed `recentHistory` straight into the LLM. Trim, summarize, and inject family context every turn so the LLM doesn't lose track on long threads.

### 4.3 Graceful Browser Use degradation
Today, if `check_d2l_grades` raises, the LLM gets a raw exception string. Catch + format: "I had trouble logging into D2L just now — try again in a minute?" Looks intentional, not broken.

### 4.4 Background notifier
The kid-notification fire-and-forget runs *after* the parent reply is sent — but if AgentPhone is up for the parent send and down for the kid send, the kid notification silently drops. Queue failed sends in SQLite and retry on a periodic background task.

### 4.5 Webhook secret + signature verification
You currently have `AGENT_PHONE_WEBHOOK_SECRET=` empty. For a public-internet demo, this is fine. Enable it post-hackathon.

### 4.6 D2L session keepalive
The dedicated Chrome profile's D2L session can expire. Add a `scripts/keepalive-d2l.sh` that opens D2L in the profile once an hour to keep cookies warm. Run in tmux during the day.

---

## Recommended order for the next 6 hours

| Time | Task | Tier |
|---|---|---|
| 0:00 – 0:15 | Seed demo family + canned-grade fallback flag | 1.1, 1.2 |
| 0:15 – 0:30 | Interstitial text on tool call | 1.3 |
| 0:30 – 1:30 | Supermemory integration | 2.1 |
| 1:30 – 3:30 | Stripe purchase-approval flow | 2.2 |
| 3:30 – 4:00 | `/dashboard` route with iMessage-style mirror | 1.5 |
| 4:00 – 4:30 | Demo script + slides | 1.4 |
| 4:30 – 5:00 | Practice run end-to-end, time it | — |
| 5:00 – 5:30 | Backup recording + last polish | 1.6 |
| 5:30 – 6:00 | Submit + buffer | — |

Skip anything in Tier 3 unless the above finishes early. **Aim for first place, not just sponsor tracks** — but bagging two extra sponsor prizes is a strong consolation.

---

## What NOT to do

- Don't refactor working code. The current architecture is fine for a demo.
- Don't add voice. Out of scope, distracts from the text flow.
- Don't deploy to Vercel. Local + ngrok is faster.
- Don't migrate from SQLite to Postgres. One family.
- Don't write tests. There's no time and tests don't win hackathons.
- Don't optimize Browser Use prompt forever. ~80% reliable is fine; fallback handles the rest.

---

## Demo-day risk checklist (run 30 min before submission)

- [ ] ngrok / lt URL stable; webhook in AgentPhone dashboard matches
- [ ] D2L session in `./chrome-profile` is fresh (log in again if >2hrs old)
- [ ] AgentPhone `/v1/messages` returning 200 (their /v1/agents/{id} too)
- [ ] iMessage delivery confirmed end-to-end to demo phone
- [ ] Demo family pre-seeded in DB; `scripts/status.sh` shows 2 users verified
- [ ] Backup recording saved + reachable from the demo laptop
- [ ] Phone screens cleared of notifications and on Do Not Disturb except FamilyOps
