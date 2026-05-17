# Demo Runbook — Grade Check Flow

**Read on stage. Every text below is copy-pasteable.**

---

## The Cast

| Role | Number | Who holds it |
|---|---|---|
| **AgentPhone** (the agent) | `+1 (412) 654-3597` | nobody — it's the bot. Both human phones text *this* number. |
| **Parent phone** (Jacob) | `+1 (613) 985-9829` | demo driver |
| **Kid phone** (Gabe) | `+1 (786) 587-3754` | second phone — held by you or a teammate |

Both phones must have **iMessage on** with the AgentPhone number (blue bubbles). SMS works as a fallback but requires 10DLC registration which we have not set up.

---

## Pre-Flight Checklist (T-10 min)

Run these in order. Stop at the first failure.

### 1. Prime the Chrome profile with D2L
```bash
# In a terminal:
open -na "Google Chrome" --args --user-data-dir="$PWD/chrome-profile"
```
- In the window that opens: visit https://learn.uwaterloo.ca/d2l/
- Log in fully (complete Duo / 2FA if prompted)
- Confirm you can see the homepage with course tiles
- **Cmd+Q to quit Chrome completely** (don't just close the window — that won't release the profile lock)

### 2. Reset to a clean slate
```bash
scripts/stop.sh
scripts/reset-db.sh
scripts/start.sh
```
Output should include `webhook registered: ... status=active`.

### 3. Confirm everything is healthy
```bash
scripts/status.sh
```
Expect:
- `server: running`
- `tunnel: running`
- `tunnel url: https://...` (write this down — it'll appear in iMessage live previews)
- `familyops.db: 0 families, 0 users` (clean)
- AgentPhone webhook `status: active`

### 4. Smoke-test the wire end-to-end
From **parent phone**, text `+14126543597`:
> ping
>
You should get *something* back within 5 seconds (the LLM will likely ask you to register). If you get nothing, the webhook is broken — abort and re-run `scripts/restart.sh`.

Then reset state again:
```bash
scripts/reset-db.sh
```

You're ready.

---

## The Demo Script (~90 seconds)

### Segment 1 — Hook (10s, you talking)
> "Parents juggle six school portals, fifty emails about permission slips, and a phone constantly buzzing with kid requests. We built one AI phone number that handles it. Watch."

### Segment 2 — Onboarding (20s)

**From parent phone, send to `+14126543597`:**
> Hey, I'm Jacob — register my kid Gabe at +17865873754

**Expected reply on parent phone (within ~5s):**
> Got it Jacob — texting Gabe now to confirm.
*(actual wording will vary; LLM phrases it on the fly)*

**Watch the kid phone — it should get within ~5s:**
> Hi Gabe, your parent Jacob just registered you with FamilyOps so they can help with school stuff like checking your grades. Reply YES to confirm this is you.

**From kid phone, send back:**
> YES

**Expected on kid phone:**
> Thanks Gabe — you're all set.

**Expected on parent phone:**
> Gabe is verified. Try asking 'what are Gabe's grades?'

### Segment 3 — Grade Query (40s)

**From parent phone:**
> what are Gabe's grades?

**Within 1-3s, parent phone gets an interstitial:**
> Checking now…
*(this comes from the LLM writing content alongside the tool call — flavor varies)*

**Then on stage, on the demo laptop:**
- A Chrome window opens up live
- Browser Use navigates to `learn.uwaterloo.ca/d2l/`
- It clicks into the Grades section, visits courses
- Total time: ~20–40s

**Final reply on parent phone:**
> Gabe's current grades:
> CS 246: 87%
> MATH 239: 92%
> STAT 230: 78%
> ENGL 109: 85%
> Lowest: STAT 230 at 78%.
*(actual numbers come from real D2L)*

**Simultaneously the kid phone shows:**
> FYI Jacob just checked your grades.

### Segment 4 — Close (20s, you talking)
> "Same architecture handles permission slips, kid purchase approvals, and school emails. Anywhere parents waste time on logistics, this agent goes. Today it's grades — that's where we started because it's the loudest pain. The phone is open."

---

## What the Judges See on Screen

Set up before stepping up:

- **Left half of laptop screen:** the demo laptop's `chrome-profile` Chrome (will be empty until you trigger the grade query, then Browser Use opens it).
- **Right half:** mirror one phone (iPhone Mirroring → Continuity, or just stand the phone up under the document camera).
- Don't try to mirror both phones — pick the parent phone. You can hold the kid phone up briefly when the FYI notification fires.

---

## Failure Modes & On-Stage Recovery

### "Got it Jacob..." never arrives
Cause: AgentPhone outbound is 502'ing (we hit this earlier today).
**Recover:**
- Don't panic on stage.
- Say "the messaging vendor is having a moment — let me show you the captured flow" and switch to a screen recording you took during pre-flight.

### Kid never gets the verification text
Same root cause (AP outbound flaky). After the demo:
```bash
scripts/resend-verification.sh
```
…retries with backoff.

### Browser Use opens Chrome but can't find Grades
Cause: D2L session expired.
**Recover:**
- Pre-flight again: open Chrome with `--user-data-dir="$PWD/chrome-profile"`, re-log into D2L, Cmd+Q.
- Don't do this on stage. Use your backup recording.

### "Chrome profile is locked" error in server logs
Cause: a Chrome window is open against `./chrome-profile`.
**Recover:**
- `pkill -f "Google Chrome.*chrome-profile"` then retry. Fast.

### iMessage shows green bubble instead of blue
Either the AgentPhone number isn't on iMessage for that contact, or it's degrading to SMS. SMS is blocked by 10DLC right now. **You'll see no message arrive.** Pivot to backup recording.

### LLM picks the wrong tool / sends a weird reply
`gpt-5.4-nano` can be hit-or-miss. **Quick fix before demo:**
```bash
echo "ORCHESTRATOR_MODEL=gpt-5.4-mini" >> .env
scripts/restart.sh
```
Buys you a smarter model on the same OpenAI key. Worth doing in pre-flight if budget allows.

---

## After the Demo

```bash
scripts/status.sh
```
…should show 1 family, 2 verified users, and the most recent grade check in the log:
```bash
scripts/logs.sh | tail -50
```

That's the receipt — judges can come ask to see real data, and we have it.

---

## Optional: Memory Beat (only if `SUPERMEMORY_API_KEY` is set in `.env`)

Add this line to the parent's first message:

> Hey, I'm Jacob — register my kid Gabe at +17865873754. **Also remember Gabe is in 2A CS at Waterloo and his tutor is on Tuesdays.**

The LLM will call `register_family` AND `remember_fact` in one turn.

Then in Segment 3, instead of just "what are Gabe's grades?", ask:
> what's Gabe up to this term, and how's he doing?

The agent should pull both the school_info memory ("2A CS at Waterloo") AND the live grades, and weave them together:
> Gabe is in 2A CS at Waterloo. Current grades: ... Lowest is STAT 230 at 78%. With his Tuesday tutor, that's the one to focus on.

If memory is *not* enabled yet (SUPERMEMORY_API_KEY empty in .env), skip this beat — the agent will silently ignore the "also remember" clause.

---

## One-Page Cheat Sheet (print this)

```
AgentPhone (text it):  +1 412-654-3597
Parent phone (texts from):  +1 613-985-9829  (you)
Kid phone (texts from):     +1 786-587-3754  (teammate)

T-10  scripts/stop.sh && scripts/reset-db.sh && scripts/start.sh
T-5   open Chrome --user-data-dir=./chrome-profile, log into D2L, Cmd+Q
T-2   text "ping" from parent → confirm reply

DEMO:
[parent → bot] Hey, I'm Jacob — register my kid Gabe at +17865873754
[kid → bot]    YES
[parent → bot] what are Gabe's grades?
[wait ~30s, Browser Use runs visibly]
```
