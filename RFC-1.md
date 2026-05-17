# RFC-1: Live Browser Streaming + Real-Time Progress

**Status:** **Planned — NOT yet implemented.** Code currently follows [RFC.md](RFC.md) (local `browser-use` OSS package). This RFC is a *migration* — if implemented, it replaces the local browser stack, it doesn't run alongside.
**Date:** 2026-05-17
**Depends on:** RFC.md (core MVP, already shipped)
**Conflicts with:** RFC.md §7 (local Browser Use) — this RFC removes that and routes through Browser Use Cloud SDK instead.

> **What this RFC adds if shipped:** parent gets a live URL to watch the agent work + iMessage step-by-step updates. Closes the 20–40s dead-air window during a grade check.

> **What this RFC trades off:** D2L auth moves from a local Chrome profile (which we own outright) to a Browser Use Cloud profile (which we re-sync before each demo). Higher demo risk if profile-sync misses Shibboleth/Duo cookies. Test thoroughly before committing.

---

## Problem

After the parent asks "what are Alex's grades?", the agent dispatches a browser task that takes 20-40 seconds. During this time the parent sees nothing — dead air. There's no feedback that work is happening, and no visibility into what the agent is doing.

## Solution

Migrate from local Browser Use (OSS) to **Browser Use Cloud SDK**. This gives us three things:

1. **`live_url`** — a read-only browser stream URL, available instantly on session creation. We wrap this in a custom page with zoom support and no interaction, then text the link to the parent.
2. **Step-by-step streaming** — the SDK yields messages as the agent acts. We forward summaries to the parent as iMessage updates in real time.
3. **Profile sync** — Browser Use Cloud has a profile upload system. We sync local Chrome cookies (D2L session) to a cloud profile, so the cloud browser inherits the D2L login.

## Architecture Change

```
BEFORE (local):
  parent asks grades
  → orchestrator calls check_d2l_grades tool
  → local Chrome opens, Browser Use agent runs locally
  → 30s of silence
  → grades returned to parent

AFTER (cloud + streaming):
  parent asks grades
  → orchestrator calls check_d2l_grades tool
  → cloud session created, live_url returned immediately
  → parent gets iMessage: "Checking grades now — watch live: https://familyops.app/live/abc123"
  → as agent navigates D2L, parent gets iMessages: "Opening course list...", "Reading CS 136 grades...", etc.
  → grades returned to parent as final message
  → kid gets FYI notification (unchanged)
```

---

## Detailed Design

### 1. Package Migration

**Remove:** `browser-use` (OSS, local browser)
**Add:** `browser-use-sdk` (cloud SDK, v3 API)

```python
# OLD
from browser_use import Agent, BrowserSession, ChatBrowserUse

# NEW
from browser_use_sdk.v3 import AsyncBrowserUse
```

### 2. Profile Sync (D2L Cookies)

Browser Use Cloud has a Profiles API. One-time setup:

```bash
export BROWSER_USE_API_KEY=bu_...
curl -fsSL https://browser-use.com/profile.sh | sh
```

This uploads cookies from the local Chrome profile (including `d2lSessionVal`, `d2lSecureSessionVal`) and returns a `profile_id`. Store it in `.env`:

```
BROWSER_USE_PROFILE_ID=prof_...
```

**Risk:** D2L sessions expire after ~20-30 min of inactivity. Before each demo:
1. Log into D2L locally in Chrome
2. Re-run the profile sync script
3. Update `BROWSER_USE_PROFILE_ID` if it changes (it shouldn't — same profile gets updated)

### 3. Cloud Browser Session with Streaming

Replace `browser_agent.py` entirely. Two functions — `create_d2l_session()` returns the
live link instantly, `stream_until_done()` blocks while polling for step updates and the
final result. This split lets us text the parent the live link before the agent even starts
navigating.

```python
import asyncio
import logging
from typing import Callable, Optional

from browser_use_sdk.v3 import AsyncBrowserUse
from config import BROWSER_USE_API_KEY, BROWSER_USE_PROFILE_ID, BROWSER_TIMEOUT_SECONDS

log = logging.getLogger(__name__)
client = AsyncBrowserUse(api_key=BROWSER_USE_API_KEY)

D2L_TASK_TEMPLATE = (
    "You are logged into University of Waterloo D2L (Brightspace) as {student_name}. "
    "Navigate to https://learn.uwaterloo.ca/d2l/. Find the Grades section. "
    "Visit each current-term course and read the Grades page. "
    "Extract every course code with its current overall grade or percentage. "
    "Return plain-text summary, one course per line: 'COURSE_CODE: GRADE'. "
    "End with a single-line note identifying the lowest-performing course."
)


async def create_d2l_session(student_name: str) -> tuple[str, str]:
    """Create cloud session, return (session_id, live_url) immediately."""
    session = await client.sessions.create(
        task=D2L_TASK_TEMPLATE.format(student_name=student_name),
        model="claude-sonnet-4.6",
        profile_id=BROWSER_USE_PROFILE_ID,
    )
    log.info("Cloud session %s created, live_url=%s", session.id, session.live_url)
    return str(session.id), session.live_url


async def stream_until_done(
    session_id: str,
    on_step: Optional[Callable] = None,
    timeout: float = BROWSER_TIMEOUT_SECONDS,
) -> str:
    """Poll messages until session completes or timeout. Returns final output."""
    import time
    deadline = time.monotonic() + timeout
    cursor = None

    while time.monotonic() < deadline:
        msgs = await client.sessions.messages(session_id, after=cursor, limit=100)
        for m in msgs.messages:
            cursor = str(m.id)
            if on_step and m.summary:
                await on_step(m.summary)

        s = await client.sessions.get(session_id)
        if s.status.value in ("idle", "stopped", "error", "timed_out"):
            if not s.output:
                raise RuntimeError(
                    f"Session {session_id} ended with status={s.status.value}, no output."
                )
            return str(s.output)
        await asyncio.sleep(2)

    raise TimeoutError(f"Browser Use session {session_id} timed out after {timeout}s")
```

### 4. Live View Page

Host a minimal page at `GET /live/{session_id}` that:
- Embeds the Browser Use `live_url` in a full-screen iframe
- Disables pointer events on the iframe (purely observational — the live view is already
  read-only server-side, but `pointer-events: none` adds client-side safety)
- Adds pinch-to-zoom support via viewport meta tag
- Shows a header: "FamilyOps — checking grades..."

**Routing:** `_live_sessions` dict in `main.py` maps `session_id → live_url`. Populated by
the tool dispatcher via `ctx["live_sessions"]`. The `/live/{session_id}` route looks it up.
No database needed — these are ephemeral (cleared on restart is fine).

```python
# In main.py
from fastapi.responses import HTMLResponse

# In-memory mapping of session_id -> live_url
_live_sessions: dict[str, str] = {}

LIVE_PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, user-scalable=yes">
  <style>
    body {{ margin: 0; background: #111; color: #fff; font-family: system-ui; }}
    header {{ padding: 12px 16px; font-size: 14px; opacity: 0.7; }}
    .viewer {{
      width: 100%; height: calc(100vh - 44px);
      overflow: auto; -webkit-overflow-scrolling: touch;
    }}
    iframe {{
      width: 100%; height: 100%; border: none;
      pointer-events: none;
    }}
  </style>
</head>
<body>
  <header>FamilyOps — checking grades...</header>
  <div class="viewer">
    <iframe src="{live_url}&theme=dark&ui=false" allow="autoplay"></iframe>
  </div>
</body>
</html>"""

@app.get("/live/{session_id}")
async def live_view(session_id: str):
    live_url = _live_sessions.get(session_id)
    if not live_url:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return HTMLResponse(LIVE_PAGE_HTML.format(live_url=live_url))
```

The parent receives: `"Checking grades now — watch live: https://{PUBLIC_URL}/live/{session_id}"`

**Why not just text the raw `live_url`?** The raw Browser Use live view shows a fake Chrome
with an interactive-looking URL bar and tab strip. Our wrapper hides that (`ui=false`),
disables interaction, and gives us a branded experience with zoom support.

### 5. Real-Time iMessage Updates

The flow in `tools.py` when dispatching `check_d2l_grades`:

1. `create_d2l_session()` → get `session_id` + `live_url` instantly
2. Store `session_id → live_url` in `ctx["live_sessions"]` (passed through from `main.py` — no circular import)
3. Text the parent the live link immediately
4. `stream_until_done()` → poll steps, forward summaries as iMessages (capped at 5)
5. Return the final grade output to the orchestrator

```python
# In tools.py, check_d2l_grades dispatch:
from browser_agent import create_d2l_session, stream_until_done
from config import PUBLIC_URL

async def _dispatch_check_grades(sender_phone: str, student_name: str, ctx: dict) -> str:
    # 1. Create session — returns instantly
    session_id, live_url = await create_d2l_session(student_name)

    # 2. Register for /live/{session_id} route
    ctx["live_sessions"][session_id] = live_url

    # 3. Text parent the live link before agent starts navigating
    await send_message(
        sender_phone,
        f"Checking now — watch live: {PUBLIC_URL}/live/{session_id}",
    )

    # 4. Stream steps as iMessages
    steps_sent = 0
    MAX_STEP_MESSAGES = 5

    async def on_step(summary: str):
        nonlocal steps_sent
        if steps_sent < MAX_STEP_MESSAGES:
            await send_message(sender_phone, summary)
            steps_sent += 1

    # 5. Block until done
    grades = await stream_until_done(session_id, on_step=on_step)
    ctx["notify_kid_about_grades"] = True
    return grades
```

The parent sees:
```
Checking now — watch live: https://abc.ngrok.app/live/sess_123
Navigating to D2L homepage...
Opening CS 136 grades page...
Found grade: CS 136 — 87%
Opening MATH 137 grades page...
Found grade: MATH 137 — 72%
[final grade summary]
```

### 6. Config Changes

New env vars in `.env`:

```
BROWSER_USE_PROFILE_ID=prof_...                       # from profile sync script
PUBLIC_URL=https://populate-stem-goggles.ngrok-free.dev  # current ngrok URL
```

`PUBLIC_URL` is set automatically by `scripts/start.sh` (written to `/tmp/familyops-tunnel-url`) so we can fall back to reading the file if `.env` is stale.

`config.py` additions:

```python
BROWSER_USE_PROFILE_ID = _env("BROWSER_USE_PROFILE_ID")
PUBLIC_URL = _env("PUBLIC_URL", default="http://localhost:8000")
```

---

## Files Changed

| File | Change |
|---|---|
| `browser_agent.py` | Full rewrite — cloud SDK, split into `create_d2l_session()` + `stream_until_done()` |
| `tools.py` | Update check_d2l_grades dispatch: create session, send live link, stream steps via `ctx` |
| `main.py` | Add `GET /live/{session_id}` route, `_live_sessions` dict, pass into `ctx` |
| `config.py` | Add `BROWSER_USE_PROFILE_ID`, `PUBLIC_URL` |
| `requirements.txt` | Replace `browser-use` with `browser-use-sdk` |
| `.env.example` | Add new env vars |

**Circular import avoidance:** `_live_sessions` dict lives in `main.py` and is passed into
the tool dispatcher via the `ctx` dict (`ctx["live_sessions"] = _live_sessions`). This reuses
the existing `ctx` plumbing — no new modules, no circular imports.

---

## Migration Steps (Implementation Order)

### Phase 1: Cloud SDK swap
1. `pip install browser-use-sdk` / remove `browser-use` from requirements
2. Rewrite `browser_agent.py` to use `AsyncBrowserUse` client
3. Run profile sync script, get `profile_id`, add to `.env`
4. Test: cloud agent can open D2L and read grades

### Phase 2: Live view + streaming
5. Add `/live/{session_id}` route with iframe viewer page
6. Wire up on_step callback in tools.py to send iMessage updates
7. Send live_url to parent before agent starts navigating
8. Test: parent receives link + step updates + final grades

### Phase 3: Polish
9. Cap step messages (max 5) to avoid spam
10. Handle session timeout / error gracefully
11. Update README with new setup steps

---

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| D2L session expires in cloud profile | High | Re-sync profile right before demo. Keep demo tight. |
| Profile sync doesn't capture all needed cookies (Shibboleth, Duo) | High | Test immediately. Fallback: use CDP to manually export + inject cookies. |
| Browser Use Cloud latency > local | Medium | Cloud has faster infra than a laptop. Likely net-neutral or faster. |
| Step summaries too verbose / noisy | Low | Cap at 5 messages. Filter to meaningful summaries only. |
| ngrok URL changes on restart | Low | Update `PUBLIC_URL` in `.env` and re-register webhook. |
| Profile sync script URL unverified | Low | Verify `https://browser-use.com/profile.sh` exists before demo. Fallback: manual cookie export via CDP. |
| Cloud session left running | Low | Don't set `keep_alive=True`. Sessions auto-stop on task completion. |

---

## What We're NOT Doing

- Custom web app beyond the single iframe viewer page
- Voice support
- Persistent conversation history
- Any changes to onboarding flow
- Kid-facing live view (only parent sees it)

## What We're Removing

- `browser-use` OSS package (replaced by `browser-use-sdk`)
- `playwright install chromium` setup step (no local browser needed)
- Local Chrome profile dir (`./chrome-profile`) — still needed for cookie sync source, but no longer used at runtime
- `BROWSER_TIMEOUT_SECONDS` usage via `asyncio.wait_for` — replaced by `stream_until_done` timeout parameter
