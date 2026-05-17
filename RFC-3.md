# RFC-3: Realtime Voice Agent

**Status:** Draft
**Date:** 2026-05-17
**Depends on:** RFC.md (core MVP), RFC-1.md (live browser streaming)

---

## 1. Problem

The SMS-only agent works, but voice is a different shape of access — not just a "format":

- **Hands-free moments.** Parents in the car or at the school pickup can't safely type. "What are Alex's grades?" should be hands-free.
- **Synchronous clarification.** Text forces a full round-trip every time the agent needs to disambiguate. Voice lets the agent ask "spring term or all-time?" in the same beat.
- **The dead-air problem inverts.** A 20–40s wait during grade-fetch is fine on text — it's just a delayed reply. On a call it's a dropped customer. The window that's neutral over SMS is fatal over voice.
- **Demo legibility.** This hackathon is literally *Call My Agent*. A judge dialling the number and watching a kid get a verification text mid-call is more legible than reading a transcript on a laptop.

The hard subproblem inside this is the grade-fetch window. The cheap voice shapes (voicemail-to-text, voice-as-SMS) all collapse there. This RFC commits to a **realtime conversational** shape where Browser Use's progress stream becomes **live spoken narration**, eliminating dead air during the fetch.

---

## 2. Solution — at a glance

A second AgentPhone agent — **voice-only** — bound to the same number as the SMS agent. Its system prompt is voice-tuned. Its tool surface is the same shape as SMS plus four voice-specific tools.

The key insight that lets us do this **without any new vendor or mid-call "say" API** is:

> **The voice agent's own model drives narration by polling a progress tool and speaking what it gets back.**

We don't need to push speech into the call from our server. We give the model a `get_grade_progress(handle)` tool, a system prompt that tells it to call that tool every few seconds during a fetch and read the result aloud, and the natural model-speaks-then-calls-tool-then-speaks loop produces continuous live narration. AgentPhone's existing tool-calling support for voice agents (which RFC-0 already relies on for SMS) is the only API we need.

Everything else is reused:

- Same SQLite schema, same `families`/`users` tables.
- Same Browser Use Cloud session (per RFC-1) for the actual D2L scrape.
- Same AgentPhone `send_message` for kid SMS.
- Same `register_family`/`confirm_kid` semantics.

---

## 3. Why this stack (and not something else)

The stack constraint for this RFC is: **only the three APIs already wired into `.env`** — AgentPhone, OpenAI (chat completions), Browser Use. No Twilio, no OpenAI Realtime, no Vapi, no LiveKit, no extra vendors.

Within that constraint, three design questions:

**3.1 — Who runs the audio loop?**

Only AgentPhone speaks PSTN. So AgentPhone is the audio runtime. Settled.

**3.2 — Who's the brain during the call?**

Two options:

- **(A) AgentPhone-managed voice agent.** Register the system prompt + tool list with AgentPhone via `POST /v1/agents`. AgentPhone runs the model loop internally, hits our tool endpoints over webhook, speaks the result. This is the same model AgentPhone uses for any voice agent on their platform — already battle-tested.
- **(B) We run the brain ourselves.** AgentPhone streams transcripts to us via WebSocket, we run our own OpenAI chat-completions loop, push replies back. The model has access to our tools natively. But — chat-completions isn't a realtime model. ASR/TTS latency stack up. We'd hit 2–4s turn times. Voice feels broken.

**Commit to (A).** AgentPhone-managed voice agent is the only way to keep sub-second turns using the APIs we have.

**3.3 — How does narration happen during the 30s grade fetch?**

The model is the agent's voice. So we need the model to keep talking during the fetch. Two ways to make that happen:

- **(i) Server-pushed narration.** Our server emits speech into the call. Requires AgentPhone to expose a mid-call "say this text now" endpoint. **Status: not confirmed in the docs.** Ruled out.
- **(ii) Model-pulled narration.** Our server only exposes tools. The voice prompt instructs the model to repeatedly call a `get_grade_progress` tool every few seconds during a long fetch, and read each new progress line aloud. The model's own ASR→LLM→TTS loop drives narration naturally.

**Commit to (ii).** It uses nothing AgentPhone doesn't already provide for any tool-using voice agent. The pattern is: model speaks, model calls tool, tool returns next progress chunk, model speaks the chunk, model calls tool again. Loops cleanly until tool returns `status="done"`.

---

## 4. Architecture

### Before (RFC-0 + RFC-1)

```
Parent ──SMS──→ AgentPhone ──webhook──→ FastAPI /webhook ──→ Orchestrator
                                                                  │
                                                  ┌───────────────┼─────────────┐
                                                  ▼               ▼             ▼
                                              SQLite       send_message    Browser Use Cloud
```

### After (this RFC)

```
                            ┌──────────────────────────┐
        Parent ──Voice─────→│   AgentPhone Number      │←──SMS── Kid
        Parent ──SMS───────→│   (two agents bound)     │←──SMS── Parent (handoff fallback)
                            └──────┬────────────┬──────┘
                                   │            │
                voice agent events │            │ sms agent events
                ┌──────────────────┘            └────────────────┐
                ▼                                                ▼
   ┌─────────────────────────┐                       ┌─────────────────────────┐
   │  /webhook/voice         │                       │  /webhook (SMS)         │
   │  - call.started         │                       │  - agent.message        │
   │  - call.transcript      │                       │  (RFC-0 unchanged)      │
   │  - call.tool_call (★)   │                       └────────────┬────────────┘
   │  - call.ended           │                                    │
   └────────────┬────────────┘                                    │
                │                                                 │
                ▼                                                 ▼
   ┌─────────────────────────┐                       ┌─────────────────────────┐
   │  Voice tool dispatcher  │                       │  SMS orchestrator       │
   │  (synchronous reply     │                       │  (OpenAI chat-          │
   │   to tool_call event)   │                       │   completions loop)     │
   └────────────┬────────────┘                       └────────────┬────────────┘
                │                                                 │
                └────────────────────┬────────────────────────────┘
                                     ▼
                          ┌─────────────────────┐
                          │  Shared tool layer  │
                          │  - register_family  │
                          │  - confirm_kid      │
                          │  + voice extras     │
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼───────────────────────┐
              ▼                      ▼                       ▼
       ┌─────────────┐      ┌─────────────────┐    ┌─────────────────────┐
       │   SQLite    │      │  AgentPhone     │    │   Browser Use Cloud │
       │  families   │      │  send_message   │    │   (per RFC-1):      │
       │  users      │      │  (SMS to kid    │    │   create_session,   │
       │             │      │   or parent)    │    │   stream messages   │
       └─────────────┘      └─────────────────┘    └──────────┬──────────┘
                                                              │
                                            voice_state mirror│
                                                              ▼
                                              ┌─────────────────────────┐
                                              │  voice_state.py         │
                                              │  {handle: {status,      │
                                              │    steps_emitted,       │
                                              │    queue_unread,        │
                                              │    final_summary}}      │
                                              └─────────────────────────┘
```

★ `call.tool_call` is the synchronous webhook. AgentPhone holds the call audio while waiting for our HTTP response, so this needs to return fast (≤500ms) — that's the entire reason the narration tools just read from `voice_state` and return immediately.

---

## 5. Detailed Design

### 5.1 Voice agent provisioning

Provision a second AgentPhone agent via `POST /v1/agents` (already in their docs). Bind it to the existing number as the voice handler.

```bash
curl -X POST https://api.agentphone.ai/v1/agents \
  -H "Authorization: Bearer $AGENT_PHONE_API_KEY" \
  -H "Content-Type: application/json" \
  -d @kiddio-voice-agent.json
```

`kiddio-voice-agent.json`:

```json
{
  "name": "kiddio-voice",
  "channel": "voice",
  "system_prompt": "...see §5.3...",
  "tools": [ ...see §5.4... ],
  "interruptible": true,
  "max_call_seconds": 600,
  "webhook_url": "https://<ngrok>/webhook/voice"
}
```

(Field names approximated against AgentPhone's docs index. Confirmed by `POST /v1/agents` reference page in Phase 1.)

Then attach to the existing number:

```bash
curl -X POST https://api.agentphone.ai/v1/numbers/$AGENT_PHONE_NUMBER_ID/agents \
  -H "Authorization: Bearer $AGENT_PHONE_API_KEY" \
  -d '{"agent_id": "<voice_agent_id>", "channel": "voice"}'
```

After this: inbound SMS still routes to `kiddio-sms` (unchanged); inbound calls route to `kiddio-voice`. Both webhook into our single FastAPI process at different URL paths.

The voice agent's id is stored in `.env` as `VOICE_AGENT_ID`.

### 5.2 Webhook routing

Voice introduces four new event types we handle at `POST /webhook/voice`:

| Event | When | Sync or async? |
|---|---|---|
| `call.started` | Inbound call answered | Async — fire-and-forget context setup |
| `call.transcript` | User turn finalized | Async (informational — AgentPhone is driving) |
| `call.tool_call` | Voice agent invokes a tool | **SYNC** — must return tool result in HTTP body |
| `call.ended` | Hangup / timeout / error | Async — cleanup |

Skeleton:

```python
# main.py additions

@app.post("/webhook/voice")
async def voice_webhook(request: Request, background_tasks: BackgroundTasks, ...):
    raw = await request.body()
    if not verify_signature(raw, ts_hdr, sig_hdr):
        raise HTTPException(401)
    payload = json.loads(raw)
    event = payload.get("event")

    if event == "call.started":
        background_tasks.add_task(on_call_started, payload)
        return {"ok": True}

    if event == "call.transcript":
        # AgentPhone drives — we log for audit, don't dispatch
        log.info("transcript: %s", payload.get("data", {}).get("text"))
        return {"ok": True}

    if event == "call.tool_call":
        # SYNCHRONOUS — must return tool result in HTTP body
        return await dispatch_voice_tool(payload)

    if event == "call.ended":
        background_tasks.add_task(on_call_ended, payload)
        return {"ok": True}

    return {"ok": True}
```

`dispatch_voice_tool` reads `tool_name`, `arguments`, `call_id`, `from_number`, looks up the caller from DB, and invokes the right tool implementation. Returns `{"tool_call_id": ..., "output": <string-or-json>}`.

### 5.3 Voice-mode system prompt

The SMS prompt is wrong for voice: it mentions iMessage, suggests structured replies, and assumes a reader not a listener. The voice prompt is rewritten end-to-end:

```
You are Kiddio, a voice assistant that families call for school logistics.

You're talking to one of three kinds of callers:
1. UNKNOWN — a new parent. Collect their name, kid's name, and kid's phone
   number, then call register_family. If anything's missing, ask ONE short
   follow-up. Don't ask for everything at once.
2. VERIFIED PARENT — they want a grade check or registration of another
   action. Call the right tool.
3. KID — kids don't call this number. If a known kid calls, say "Only your
   parent uses me right now" and end the call.

STYLE FOR VOICE:
- One short sentence per turn. Aim for 8–14 words.
- No markdown. No lists. No emojis. No URLs.
- Speak numbers naturally: "eighty-seven percent in CS246", not "CS246: 87%".
- Acknowledge before working: "got it", "one sec", "checking now".
- Confirm digits before calling register_family — voice transcription
  mishears. Say "so that's four one five, five five five, one two three
  four — right?" before invoking.
- If the parent goes silent for >6 seconds, ask "still there?"
- When you need to send a text, tell them: "I'll text you the details."

CRITICAL — HOW TO HANDLE LONG TOOL CALLS:

The tool `check_d2l_grades` is special. When you call it:

  1. It returns one of: {"status": "starting", "handle": "..."} (just kicked
     off), {"status": "running", "step": "...", "handle": "..."} (in progress
     with a new step to speak), or {"status": "done", "summary": "...",
     "handle": "..."} (finished, here is the final answer).

  2. If status is "starting" or "running":
     - Speak the step naturally if one was returned ("looking at CS246,
       eighty-seven percent").
     - Then IMMEDIATELY call check_d2l_grades AGAIN with the same handle.
     - Repeat until status is "done".
     - Do NOT fall silent between calls. Always either speak a step or say
       a short filler ("still going", "almost there") if no new step came
       back.

  3. When status is "done", speak the summary in one sentence
     ("Alex is averaging eighty-fives, lowest is statistics at seventy-eight"),
     then ask "anything else?".

This polling pattern is HOW the user hears live progress. Do not skip it.
Do not wait for "the final result" without polling — there is no final
result without polling.

WHAT NOT TO DO:
- Don't read full tool output verbatim — paraphrase.
- Don't list every course unless asked.
- Don't promise actions you didn't take.
- Don't end the call without confirming the user is done.
```

The "CRITICAL" block is doing most of the work. It's verbose on purpose — voice models will follow explicit instructions about tool-loop shape but will silently break the shape if it's only implied.

### 5.4 Tool surface

| Tool | SMS | Voice | Notes |
|---|---|---|---|
| `register_family` | ✓ | ✓ | Identical signature. Voice prompt requires the model to read back the phone digits first. |
| `confirm_kid` | ✓ | ✓ | Identical signature. Rarely fires during a call — kid YES is over SMS — but kept for symmetry. |
| `check_d2l_grades` | (RFC-0/1) | **REDEFINED** | Same tool name, different behavior in voice mode: polling-based, returns progress chunks. See §5.6. |
| `wait_for_kid_confirmation` | — | ✓ | Voice-only. Polls DB until the kid flips to `verified`. |
| `handoff_to_sms` | — | ✓ | Voice-only. Sends an SMS to the caller and signals the model to wrap up. |
| `end_call` | — | ✓ | Voice-only. Graceful hangup via AgentPhone's call control. |

Tool schemas:

```json
{
  "name": "check_d2l_grades",
  "description": "Check the kid's grades on D2L. CALL THIS REPEATEDLY in a loop, passing back the handle each time, until status is 'done'. Each call returns a new progress step to read aloud. Do NOT wait silently between calls.",
  "parameters": {
    "type": "object",
    "properties": {
      "student_name": {
        "type": "string",
        "description": "First name of the kid. Required on the FIRST call. Subsequent calls can omit this if you pass `handle`."
      },
      "handle": {
        "type": "string",
        "description": "Handle returned by a previous call. Pass it on every subsequent call to continue the same fetch."
      }
    }
  }
}
```

```json
{
  "name": "wait_for_kid_confirmation",
  "description": "Block for up to `timeout_seconds` waiting for the kid to text YES. Use right after register_family. The user is on the line; you may say one reassurance ('hold on') before calling this.",
  "parameters": {
    "type": "object",
    "properties": {
      "kid_phone": {"type": "string"},
      "timeout_seconds": {"type": "integer", "default": 45}
    },
    "required": ["kid_phone"]
  }
}
```

```json
{
  "name": "handoff_to_sms",
  "description": "Send the caller a text message and signal that the voice work is done. Use this when the caller wants something written down, or when a tool is taking too long.",
  "parameters": {
    "type": "object",
    "properties": {
      "body": {"type": "string"}
    },
    "required": ["body"]
  }
}
```

```json
{
  "name": "end_call",
  "description": "End the call gracefully. Only call after the caller has indicated they're done.",
  "parameters": {
    "type": "object",
    "properties": {
      "reason": {"type": "string"}
    }
  }
}
```

### 5.5 Caller identity = auth

Same trust model as SMS: the `from_number` on `call.started` is the caller's identity. Look up `users` by phone, build the same context block we use for SMS, return it to the voice agent as part of every `call.tool_call` response (under a `_context` field, or via a context-fetching tool the model is told to call once at the start).

Concretely we add one zero-side-effect tool the prompt requires the model to call exactly once on a new call:

```json
{
  "name": "get_caller_context",
  "description": "Call this ONCE at the start of every call to learn who is calling. Returns the caller's role and family info, or 'UNKNOWN' if they're a new parent.",
  "parameters": {"type": "object", "properties": {}}
}
```

That tool reads `from_number` (passed by AgentPhone in the `call.tool_call` payload) and returns a context string built the same way as `_build_context` in `agent.py` today.

**Known weakness:** caller-ID is spoofable. For grade-checks specifically — sensitive data — this is a real risk and easier to exploit than SMS-from spoofing. **Out of scope for MVP, but flagged.** Never wire a purchase-approval or signature flow to voice without revisiting this.

### 5.6 The polling-narration pattern (kernel of the design)

This is the only genuinely new pattern in the RFC. Walk it through carefully.

**State store** (`voice_state.py`):

```python
import asyncio

# call_id  ->  active grade-fetch
# handle is the Browser Use session id
_state: dict[str, dict] = {}
_lock = asyncio.Lock()

async def start(call_id: str, handle: str) -> None:
    async with _lock:
        _state[call_id] = {
            "handle": handle,
            "status": "running",
            "step_queue": [],       # unread progress lines
            "final_summary": None,
        }

async def push_step(call_id: str, step: str) -> None:
    async with _lock:
        if call_id in _state:
            _state[call_id]["step_queue"].append(step)

async def finish(call_id: str, summary: str) -> None:
    async with _lock:
        if call_id in _state:
            _state[call_id]["status"] = "done"
            _state[call_id]["final_summary"] = summary

async def next_chunk(call_id: str) -> dict:
    """Return the next chunk for the voice model to read aloud."""
    async with _lock:
        st = _state.get(call_id)
        if not st:
            return {"status": "error", "message": "no active fetch"}
        if st["step_queue"]:
            return {"status": "running",
                    "step": st["step_queue"].pop(0),
                    "handle": st["handle"]}
        if st["status"] == "done":
            return {"status": "done",
                    "summary": st["final_summary"],
                    "handle": st["handle"]}
        # running but no new step yet
        return {"status": "running",
                "step": None,
                "handle": st["handle"]}
```

**Tool implementation** (`tools.py` voice path):

```python
# Pseudocode — runs inside dispatch_voice_tool

async def voice_check_d2l_grades(*, call_id, sender_phone, args, ctx):
    handle = args.get("handle")
    student = args.get("student_name", "").strip()

    state = await voice_state.next_chunk(call_id)
    if state.get("status") != "error":
        # Active fetch — return next chunk
        return state

    # No active fetch — kick one off
    session_id, _ = await create_d2l_session(student)         # RFC-1
    await voice_state.start(call_id, session_id)
    asyncio.create_task(_pump_browser_use_into_state(
        call_id, session_id, sender_phone, student
    ))
    return {"status": "starting", "handle": session_id}

async def _pump_browser_use_into_state(call_id, session_id, sender_phone, student):
    """Background task. Runs for the duration of the fetch."""
    try:
        async def on_step(raw_step: str):
            phrase = narration.summarize_step(raw_step)
            if phrase:
                await voice_state.push_step(call_id, phrase)
        summary = await stream_until_done(session_id, on_step=on_step)  # RFC-1
        await voice_state.finish(call_id, summary)
        # Fire kid-FYI SMS (existing pattern)
        await _maybe_notify_kid_about_grades(sender_phone)
    except Exception as e:
        log.exception("grade fetch failed")
        await voice_state.finish(
            call_id,
            "Sorry, I couldn't get the grades. I'll text you when I have them."
        )
```

**What the model does** (driven by the prompt in §5.3):

```
USER:  "what are alex's grades?"
MODEL: "One sec, checking now."
       → tool_call check_d2l_grades(student_name="Alex")
TOOL:  {"status": "starting", "handle": "sess_abc"}
MODEL: "Opening D2L."
       → tool_call check_d2l_grades(handle="sess_abc")
TOOL:  {"status": "running", "step": "looking at the course list", "handle": "sess_abc"}
MODEL: "Looking at the course list."
       → tool_call check_d2l_grades(handle="sess_abc")
TOOL:  {"status": "running", "step": "CS246, eighty-seven percent", "handle": "sess_abc"}
MODEL: "CS246, eighty-seven percent."
       → tool_call check_d2l_grades(handle="sess_abc")
TOOL:  {"status": "running", "step": null, "handle": "sess_abc"}    [no new step yet]
MODEL: "Still going."
       → tool_call check_d2l_grades(handle="sess_abc")
TOOL:  {"status": "running", "step": "MATH239, ninety-two", "handle": "sess_abc"}
MODEL: "MATH239, ninety-two."
       → tool_call ... (repeats)
TOOL:  {"status": "done", "summary": "Alex is averaging high-eighties, lowest is statistics at seventy-eight."}
MODEL: "Alright, Alex is averaging high-eighties. The lowest is statistics at seventy-eight. Anything else?"
```

This is the entire narration mechanism. The model speaks → calls tool → gets next chunk → speaks the chunk → calls tool again. The chunks come from `voice_state` which is populated in the background as Browser Use streams its messages.

**Pacing.** Each model turn (speak + call) is ~1.5–2s. Each Browser Use step arrives every ~3–5s. The `step_queue` smooths this out — the model never blocks on an empty queue (it gets `step: null` and says a filler line), and never gets a backlog because steps are popped one per call.

**`narration.summarize_step`** is the same module as in the previous RFC-3 draft: a hand-tuned filter that maps Browser Use's verbose step messages to short, spoken-friendly phrases (≤12 words, no jargon).

### 5.7 Mid-call kid verification

Registration over voice has a real-time wait baked in: the parent's on the line while the kid texts back.

```
parent ──call──→ Kiddio: "Hey, I'm Jacob. Register Alex at 415 555 1234."
Kiddio: "Got it Jacob. So that's Alex at 4-1-5, 5-5-5, 1-2-3-4. Right?"
parent: "Yes."
Kiddio: "Cool, registering now. One sec."
        → register_family(parent_name="Jacob", kid_name="Alex",
                          kid_phone="+14155551234")
        [tool fires verification SMS to kid via existing send_message]
Kiddio: "Just texted Alex. Hold on while they reply."
        → wait_for_kid_confirmation(kid_phone="+14155551234", timeout_seconds=45)
        [tool polls DB every 1s; kid texts YES via SMS;
         the existing SMS path runs confirm_kid; DB flips;
         wait_for_kid_confirmation wakes]
        [tool returns: {"confirmed": true, "kid_name": "Alex"}]
Kiddio: "Alex just confirmed. You're set. Want to try a grade check?"
```

Implementation:

```python
async def voice_wait_for_kid_confirmation(*, args, **_):
    kid_phone = normalize_phone(args["kid_phone"])
    timeout = args.get("timeout_seconds", 45)
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        kid = get_user_by_phone(kid_phone)
        if kid and kid["onboarding_state"] == "verified":
            return {"confirmed": True, "kid_name": kid["name"]}
        await asyncio.sleep(1)
    return {"confirmed": False}
```

The 1s poll interval is fine — SQLite handles thousands of reads per second. The 45s timeout is well inside AgentPhone's default max-call-seconds (configured at 600).

If the kid doesn't reply in time, the tool returns `confirmed=false`. The prompt-side handling is in §5.3 ("Don't promise actions you didn't take") — the model says "Alex hasn't replied yet; I'll text you both when they do" and the call ends.

### 5.8 Handoff to SMS

Two reasons to break out of voice:

1. **User asks.** "Just text me the grades instead." Model calls `handoff_to_sms(body=<content>)` then `end_call`. Implementation: send SMS via `agentphone_client.send_message`, return `{"ok": true}`, model wraps up.
2. **Tool failure during call.** Browser Use times out, narration pump catches it, pushes a final "couldn't fetch — will text" into the step queue, marks status done. Model speaks it, hangs up. The background task continues; when (if) Browser Use eventually returns, it texts the result to the caller's number.

`handoff_to_sms` is a thin wrapper around the existing `send_message`. The added value is signaling to the model "your job is done; wrap up." The model handles the verbal close.

### 5.9 Interruption / barge-in

AgentPhone voice agents support user interruption (assumed; verify Phase 1). Implications for our design:

- During narration, if the user says "just give me the lowest grade," AgentPhone interrupts the model's speech, the next user turn comes in, the model pivots.
- Our `_pump_browser_use_into_state` background task keeps going regardless — interruption is a presentation concern, not a fetch-cancellation concern. Partial results are still useful.
- When the model resumes calling `check_d2l_grades`, it gets the next chunk from the queue (or `status: done` if the fetch completed during the interruption).

We do NOT attempt to cancel an in-flight Browser Use session on user interruption. Users say "skip ahead in the narration," not "stop trying entirely."

### 5.10 Failure modes

| Mode | Detection | Handling |
|---|---|---|
| AgentPhone voice agent doesn't support tool calling | Phase 1 smoke test | **Hard blocker.** Document at top of RFC. Fall back to "voicemail-to-text" shape: AgentPhone records, transcript fed to SMS orchestrator, reply via SMS. |
| Model doesn't follow the polling pattern (falls silent between calls) | Phase 3 dogfood | Reinforce in prompt. Add an explicit "if status is running, immediately call the tool again — never stop in the middle of a fetch" reminder. If still flaky, give the model a `narrate_progress(text)` tool it MUST call between polls. |
| Browser Use stuck | 90s wall clock in `_pump_browser_use_into_state` | Push "this is taking too long, I'll text you when ready" into step queue. Mark done. Caller gets SMS later. |
| `wait_for_kid_confirmation` times out | Tool returns `confirmed=false` | Model speaks "Alex hasn't replied yet" per prompt. Confirmation later arrives via SMS path and updates DB; both parties get SMS. |
| Call drops mid-fetch | `call.ended` arrives during `_pump_browser_use_into_state` | Background task's `voice_state.finish` becomes a no-op (call_id removed in `on_call_ended`). Send final summary via SMS to the caller's `from_number`. |
| Caller is `UNKNOWN` on call.started | `get_caller_context` returns UNKNOWN | Model runs new-parent flow (registration). |
| `call.tool_call` HTTP timeout from AgentPhone side | AgentPhone retries | Make every tool idempotent on `tool_call_id`. The polling pattern is naturally idempotent — re-calling `check_d2l_grades(handle=X)` returns the next chunk or the same one if queue hadn't advanced. |
| Two simultaneous voice calls from same caller | AgentPhone config | Reject second call via agent config (`reject_concurrent: true` or equivalent). |
| Browser Use returns no `final_result` | RFC-1 raises `RuntimeError` | Caught in `_pump_browser_use_into_state`, finishes with a generic "couldn't get the grades" message instead of crashing. |
| Caller-ID spoofing | Out of band | Out of scope; flagged. Don't add sensitive flows over voice until revisited. |

---

## 6. Files Changed

| File | Change |
|---|---|
| `main.py` | Add `/webhook/voice` route, `on_call_started`, `on_call_ended`, `dispatch_voice_tool`. SMS `/webhook` unchanged. |
| `tools.py` | Add 5 voice-specific tool functions (`voice_check_d2l_grades`, `voice_wait_for_kid_confirmation`, `voice_handoff_to_sms`, `voice_end_call`, `get_caller_context`). Existing tools unchanged. Voice tool dispatcher in a new sibling function `dispatch_voice_tool`. |
| `agentphone_client.py` | Add `end_call_api(call_id)`. No new mid-call APIs needed. |
| `browser_agent.py` | Use the `create_d2l_session` + `stream_until_done` split from RFC-1 (no further changes). |
| `narration.py` | **NEW.** `summarize_step(raw_step) -> str | None`. 8–12 hand-tuned patterns for D2L step messages. |
| `voice_state.py` | **NEW.** In-memory state store described in §5.6. |
| `config.py` | Add `VOICE_AGENT_ID`, `KID_VERIFICATION_TIMEOUT_SECONDS` (default 45), `BROWSER_USE_FETCH_TIMEOUT_SECONDS` (default 90). |
| `.env.example` | Add `VOICE_AGENT_ID=agent_...`. |
| `requirements.txt` | No new deps. |
| `README.md` | New section: "Calling the demo number — voice flow setup." |
| `db.py` | No changes. |

Note: there's a circular-import risk between `tools.py` (which now needs `voice_state`) and `main.py` (which also needs `voice_state` for `on_call_ended` cleanup). Resolved by putting `voice_state.py` in a leaf module that both import — no back-reference.

---

## 7. Implementation Phases

Time-boxed against a fresh ~10hr session, assuming RFC-0 + RFC-1 are landed.

### Phase 1 — Voice plumbing + capability check (1.5 hr)
- Provision `kiddio-voice` agent in AgentPhone (see §5.1). Bind to existing number with `channel="voice"`.
- Wire `/webhook/voice` with four event handlers.
- Implement ONE tool: `get_caller_context`. No real logic — return a fixed string.
- **CRITICAL VERIFICATIONS, in this order, all done in Phase 1:**
  1. AgentPhone voice agent supports tool calling at all (call.tool_call event fires).
  2. AgentPhone voice agent allows multi-step tool calling within a single user turn — i.e. the model can speak, tool-call, speak more, tool-call again, without ending the turn.
  3. AgentPhone voice agent supports user interruption (barge-in).
- **If 1 or 2 fail:** fall back to voicemail-to-text. Drop everything else in this RFC. Document the fallback in §10 open questions.
- **Done when:** calling the demo number, agent says "Hi Kiddio here, who's this?" — and after the caller answers, agent calls `get_caller_context` and reads back "I have you as Jacob, verified parent."

### Phase 2 — Voice registration + kid wait (2 hr)
- Voice system prompt (§5.3) — paste in, iterate.
- Implement `voice_register_family` (thin wrapper around the existing register_family in SMS dispatch — only difference is the response format).
- Implement `wait_for_kid_confirmation` (§5.7).
- Implement `end_call`.
- **Done when:** parent calls, says "register Alex at 415-…", model reads back digits, parent confirms, model invokes register_family, SMS lands on kid's phone, kid texts YES via existing SMS path, voice model hears "Alex confirmed" and says so.

### Phase 3 — Polling narration (3 hr)
- Implement `voice_state.py`.
- Implement `narration.py` with ~8 D2L-specific patterns + 2 generic fillers.
- Implement `voice_check_d2l_grades` (§5.6) — the kernel.
- Implement `_pump_browser_use_into_state` background task.
- Wire `create_d2l_session` + `stream_until_done` from RFC-1.
- Tune the prompt: if the model breaks the polling shape, reinforce the "CRITICAL" block.
- **Done when:** parent calls, asks "what are Alex's grades?", hears continuous narration (one short phrase every 2–4s) for the duration of the fetch, then a final summary. Kid receives FYI SMS.

### Phase 4 — Handoff, error paths, polish (1.5 hr)
- `handoff_to_sms`.
- Browser Use timeout → push "I'll text you" into queue → SMS deliver.
- Call drops mid-fetch → SMS fallback to caller.
- "Still there?" prompt for >6s caller silence (AgentPhone prompt setting).
- **Done when:** every failure mode in §5.10 produces a clean experience in a manual test.

### Phase 5 — Demo prep (1 hr)
- Voice quality pass: rephrase any TTS phrase that sounds robotic ("D 2 L" instead of "D2L", "C S 246" instead of "CS246" if mispronounced).
- Time the registration-over-voice end-to-end. Target: <45s including kid YES.
- Time the grade check end-to-end with narration. Target: <40s of audio, no >5s silence.
- Practice run on the demo phones (parent + kid).
- README updated.

### Phase 6 — Buffer (1 hr)

---

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| AgentPhone voice agent doesn't support tool calling | **CRITICAL** | Verify Phase 1, hour 1. If it doesn't, fall back to voicemail-to-text. The rest of the RFC is moot. |
| AgentPhone doesn't allow multi-step tool calling in one turn | **CRITICAL** | Verify Phase 1, hour 1. If single-tool-call-per-turn only, narration pattern is impossible. Fall back to "agent says 'I'll text you' then ends call → result via SMS." |
| Voice model resists the polling pattern despite the prompt | **HIGH** | Reinforce the prompt. Add a `narrate_progress(text)` tool the model MUST call between polls. Worst case: hardcode 6 generic phrases that play regardless. |
| AgentPhone realtime model latency >1s/turn | **MED** | Out of our control. If too slow, accept it — narration cadence just spreads to one phrase per 3s instead of one per 1.5s. Still better than dead air. |
| Narration patterns don't match Browser Use's actual D2L step messages | **MED** | Generic filler ("looking up grades", "still checking", "almost there", "got it") fires regardless of step content. Course-specific phrases are bonus. |
| Caller-ID spoofing | LOW (demo) / **HIGH** (prod) | Out of scope. Flag in §5.5. Never wire purchase-approval over voice. |
| Two prompts to maintain (voice + SMS) | LOW | Accepted. Voice diverges for good reasons. |
| Mid-call DB polling for kid verification adds load | LOW | One concurrent call at demo. SQLite handles 100× this. |
| Voice transcription mishears the kid's phone digits | **MED** | Prompt requires read-back before invocation. If digits are wrong, parent corrects. |
| `call.tool_call` HTTP timeout from AgentPhone | LOW | Make tool dispatch idempotent on `tool_call_id`. The polling pattern is naturally idempotent. |
| Background task survives `call.ended` | LOW | `on_call_ended` removes call_id from `voice_state`. Background task checks; on missing call_id, routes final summary to SMS instead. |
| Concurrent calls from same caller | LOW | Reject in AgentPhone agent config. One conversation per parent. |
| Cost of voice minutes during dev | LOW | AgentPhone pricing assumed acceptable for a hackathon. Cap at 600s/call. |

---

## 9. What we're NOT doing

- **Voice for the kid.** Kids never receive or place calls. Verification and FYI stay SMS.
- **Multi-party calls.** Parent + kid on the same conference is nice but adds AgentPhone conference-call complexity. Punt.
- **Voicemail or recordings.** No fallback voicemail box. No call recording for audit.
- **Voice biometrics / speaker verification.** Caller-ID stays as the only identity check.
- **Multiple languages.** English only.
- **Concurrent calls from same caller.** One at a time.
- **Persistent call context across calls.** Each call starts fresh from the DB.
- **Tool-and-talk concurrency.** Sequential by design via the polling pattern.
- **Server-pushed mid-call speech.** We rejected this path — narration is model-pulled, not server-pushed.
- **Switching SMS to anything other than AgentPhone.** SMS path stays exactly as in RFC-0.

---

## 10. Open Questions

1. **Does AgentPhone voice agent support tool calling?** Blocks everything. Phase 1, hour 1.
2. **Does it support multi-step tool calls in a single user turn?** Blocks the narration pattern. Phase 1, hour 1.
3. **Is there a configurable user-silence prompt ("still there?") in agent config**, or do we need to handle it in our prompt?
4. **What's the default `max_call_seconds`** on a new AgentPhone voice agent? Affects timeout budgets.
5. **Does AgentPhone propagate the inbound `from_number`** to every `call.tool_call` payload, or only on `call.started`? Affects whether we need a per-call cache.
6. **Voice ID** — pick one in Phase 5. Default to a warm, mid-pitch voice. Avoid anything that sounds like a phone tree.
7. **Two agents on one number** — does AgentPhone's bind-agent-to-number API support channel-specific routing (SMS → agent A, voice → agent B), or do we need a second number? If second number is required, demo flow has a "call this for voice, text this for SMS" awkwardness — acceptable but worth knowing.

---

## 11. Appendix A — Example end-to-end transcripts

### A.1 — Successful registration + grade check in one call

```
[ringing]
Kiddio: "Hi, this is Kiddio. Who's this?"
        → get_caller_context() returns UNKNOWN

Parent: "Hey, I'm Jacob. I want to set up my kid Alex. His number is
         four one five, five five five, one two three four."

Kiddio: "Got it Jacob. So that's Alex at 4-1-5, 5-5-5, 1-2-3-4 — right?"

Parent: "Yes."

Kiddio: "Cool, registering now. One sec."
        → register_family(parent_name="Jacob", kid_name="Alex",
                          kid_phone="+14155551234")
        → SMS fires to kid

Kiddio: "Just texted Alex. Hold on while they reply."
        → wait_for_kid_confirmation(kid_phone="+14155551234", timeout_seconds=45)
        [Alex texts "yes" via SMS, confirm_kid runs, state flips]
        → tool returns {"confirmed": true, "kid_name": "Alex"}

Kiddio: "Alex just confirmed. You're set. Want to try a grade check?"

Parent: "Yeah, what are his grades?"

Kiddio: "One sec, checking now."
        → check_d2l_grades(student_name="Alex")
        → {"status": "starting", "handle": "sess_abc"}
Kiddio: "Opening D2L."
        → check_d2l_grades(handle="sess_abc")
        → {"status": "running", "step": "looking at the course list"}
Kiddio: "Looking at the course list."
        → check_d2l_grades(handle="sess_abc")
        → {"status": "running", "step": "C S 246, eighty-seven percent"}
Kiddio: "CS246, eighty-seven percent."
        → check_d2l_grades(handle="sess_abc")
        → {"status": "running", "step": null}
Kiddio: "Still going."
        → check_d2l_grades(handle="sess_abc")
        → {"status": "running", "step": "MATH239, ninety-two"}
Kiddio: "MATH239, ninety-two."
        → ... (4 more rounds) ...
        → {"status": "done", "summary": "Alex is averaging high-eighties,
                                          lowest is statistics at seventy-eight"}
Kiddio: "Alright. Alex is averaging high-eighties. Lowest is statistics
         at seventy-eight. Anything else?"

Parent: "Nope, that's great."

Kiddio: "Talk soon."
        → end_call()

[Alex receives SMS: "FYI Jacob just checked your grades."]
```

### A.2 — Kid doesn't reply within timeout

```
Kiddio: "Just texted Alex. Hold on while they reply."
        → wait_for_kid_confirmation(...)
        [45s passes, no YES]
        → {"confirmed": false}

Kiddio: "Alex hasn't replied yet. I'll text you both when they do.
         Anything else for now?"

Parent: "No, that's fine."

Kiddio: "Talk soon."
        → end_call()

[Later — kid texts YES via SMS, confirm_kid runs, parent gets SMS "Alex is verified."]
```

### A.3 — Caller asks to be texted instead

```
Parent: "Actually, just text me his grades."

Kiddio: "Sure thing — one sec while I kick it off."
        → check_d2l_grades(student_name="Alex")
        → {"status": "starting", "handle": "sess_xyz"}
Kiddio: "I'll text you the summary as soon as it's ready. Anything else?"
        → handoff_to_sms(body="One sec, getting Alex's grades — I'll text
                                them over in about thirty seconds.")

Parent: "Nope, thanks."

Kiddio: "Talk soon."
        → end_call()

[Background: Browser Use finishes, summary sent as SMS to parent's number.
 Kid still gets FYI SMS.]
```

### A.4 — Browser Use stalls past 90s

```
Parent: "What are Alex's grades?"

Kiddio: "One sec, checking now."
        → check_d2l_grades(student_name="Alex")
        → starting
Kiddio: "Opening D2L."
        ... [normal narration for ~60s] ...
        [Browser Use stalled — narration pump times out at 90s,
         pushes final "couldn't fetch" into queue, marks done]

        → check_d2l_grades(handle=...)
        → {"status": "done", "summary": "Sorry, I couldn't get the grades.
                                          I'll text you when I have them."}

Kiddio: "Hm — this one's slow today. I'll text you the grades as soon as
         I have them. Anything else?"

Parent: "Just text me."

Kiddio: "On it. Talk soon."
        → end_call()

[Background fetch eventually finishes — sent via SMS. Or if it never
 finishes, parent just doesn't get the SMS; they can ask again later.]
```

---

## 12. Appendix B — Why polling-narration, structurally

The polling pattern feels weird at first — why have the model call the same tool eight times when it could just receive one streamed response?

The answer is **what stays inside the existing API surface**:

| Mechanism | Requires | Why we don't have it |
|---|---|---|
| Server pushes spoken text mid-call | AgentPhone `/v1/calls/{id}/say` (or equiv.) | Not confirmed in docs |
| Server streams a single tool's response progressively | AgentPhone tool protocol supports SSE/chunked response | Not confirmed in docs |
| Server's brain runs the audio loop | Twilio + OpenAI Realtime (or equiv.) | New vendor, ruled out by constraints |
| Voice agent's model calls a tool repeatedly | AgentPhone voice agent supports tool calling | The one thing we believe AgentPhone has |

Polling-narration is the **only path** that uses the smallest AgentPhone surface area — namely, "the voice model can call a tool, get a result, and continue the turn." If THAT doesn't work, voice as a realtime conversational shape isn't shippable on the current stack and we revert to the voicemail-to-text fallback.

If, after Phase 1, AgentPhone turns out to support a richer mechanism (mid-call say, streaming tool responses), we can replace §5.6 wholesale and keep everything else. The polling pattern is the load-bearing fallback floor.
