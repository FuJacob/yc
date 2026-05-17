# RFC-1: Live Browser Streaming + Real-Time Progress

**Status:** Draft
**Date:** 2026-05-17
**Depends on:** RFC.md (core MVP)

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

Replace `browser_agent.py` entirely:

```python
import asyncio
import logging
from browser_use_sdk.v3 import AsyncBrowserUse
from config import BROWSER_USE_API_KEY, BROWSER_USE_PROFILE_ID

log = logging.getLogger(__name__)
client = AsyncBrowserUse(api_key=BROWSER_USE_API_KEY)

async def check_d2l_grades(student_name: str, on_step=None) -> str:
    """Run cloud browser agent against D2L. Yields step updates via on_step callback."""

    task = (
        f"You are logged into University of Waterloo D2L (Brightspace) as {student_name}. "
        f"Navigate to https://learn.uwaterloo.ca/d2l/. Find the Grades section. "
        f"Visit each current-term course and read the Grades page. "
        f"Extract every course code with its current overall grade or percentage. "
        f"Return plain-text summary, one course per line: 'COURSE_CODE: GRADE'. "
        f"End with a single-line note identifying the lowest-performing course."
    )

    run = client.run(
        task,
        model="claude-sonnet-4.6",
        profile_id=BROWSER_USE_PROFILE_ID,
    )

    live_url = None
    async for msg in run:
        # Capture live_url from first message's session
        if not live_url and run.result and hasattr(run.result, 'live_url'):
            live_url = run.result.live_url

        if on_step and msg.summary:
            await on_step(msg.summary, live_url=live_url)

    result = run.result
    if not result or not result.output:
        raise RuntimeError("Browser Use Cloud did not return a result.")

    return str(result.output)
```

### 4. Live View Page

Host a minimal page at `GET /live/{session_id}` that:
- Embeds the Browser Use `live_url` in a full-screen iframe
- Disables pointer events on the iframe (purely observational)
- Adds pinch-to-zoom / scroll-to-zoom support via CSS `transform: scale()`
- Shows a header: "FamilyOps — checking grades..."

```html
<!-- Served by FastAPI at /live/{session_id} -->
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5">
  <style>
    body { margin: 0; background: #111; color: #fff; font-family: system-ui; }
    header { padding: 12px 16px; font-size: 14px; opacity: 0.7; }
    .viewer {
      width: 100%; height: calc(100vh - 44px);
      overflow: auto; -webkit-overflow-scrolling: touch;
    }
    iframe {
      width: 100%; height: 100%; border: none;
      pointer-events: none; /* read-only */
    }
  </style>
</head>
<body>
  <header>FamilyOps — checking grades...</header>
  <div class="viewer">
    <iframe src="{{ live_url }}&theme=dark&ui=false" allow="autoplay"></iframe>
  </div>
</body>
</html>
```

The parent receives: `"Checking grades now — watch live: https://{NGROK_URL}/live/{session_id}"`

### 5. Real-Time iMessage Updates

During the browser agent run, we forward step summaries as iMessages:

```python
# In tools.py, during check_d2l_grades dispatch:
steps_sent = 0
MAX_STEP_MESSAGES = 5  # Don't spam — cap at 5 intermediate messages

async def on_step(summary: str, live_url: str = None):
    nonlocal steps_sent
    if steps_sent < MAX_STEP_MESSAGES and summary:
        await send_message(sender_phone, f">> {summary}")
        steps_sent += 1

result = await check_d2l_grades(student_name, on_step=on_step)
```

This gives the parent a feed like:
```
Checking grades now — watch live: https://abc.ngrok.app/live/sess_123
>> Navigating to D2L homepage...
>> Opening CS 136 grades page...
>> Found grade: CS 136 — 87%
>> Opening MATH 137 grades page...
>> Found grade: MATH 137 — 72%
[final grade summary]
```

### 6. Config Changes

New env vars in `.env`:

```
BROWSER_USE_PROFILE_ID=prof_...   # from profile sync script
PUBLIC_URL=https://abc.ngrok.app  # ngrok URL, for live view links
```

`config.py` additions:

```python
BROWSER_USE_PROFILE_ID = _env("BROWSER_USE_PROFILE_ID")
PUBLIC_URL = _env("PUBLIC_URL", default="http://localhost:8000")
```

---

## Files Changed

| File | Change |
|---|---|
| `browser_agent.py` | Full rewrite — cloud SDK, streaming, on_step callback |
| `tools.py` | Update check_d2l_grades dispatch to pass on_step, send live_url |
| `main.py` | Add `GET /live/{session_id}` route serving the viewer page |
| `config.py` | Add `BROWSER_USE_PROFILE_ID`, `PUBLIC_URL` |
| `requirements.txt` | Replace `browser-use` with `browser-use-sdk` |
| `.env.example` | Add new env vars |

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

---

## What We're NOT Doing

- Custom web app beyond the single iframe viewer page
- Voice support
- Persistent conversation history
- Any changes to onboarding flow
- Kid-facing live view (only parent sees it)
