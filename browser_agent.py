import asyncio
import logging
import time
from typing import Any, Callable, Optional

from browser_use_sdk import AsyncBrowserUse

from config import (
    BROWSER_TIMEOUT_SECONDS,
    BROWSER_USE_API_KEY,
    BROWSER_USE_PROFILE_ID,
    D2L_PASSWORD,
    D2L_URL,
    D2L_USERNAME,
)

log = logging.getLogger(__name__)

client = AsyncBrowserUse(api_key=BROWSER_USE_API_KEY)

D2L_TASK_TEMPLATE = (
    "Navigate to {d2l_url}. "
    "If you see a login page or are redirected to a Microsoft/ADFS sign-in page, "
    "log in with email '{username}' and password '{password}'. "
    "If prompted for 'Stay signed in?' click Yes. "
    "IMPORTANT: Never include the email or password in your step summaries or output. "
    "Just say 'logging in' if you need to describe that step. "
    "Once logged in, click on 'Grades' in the nav bar. "
    "You will see a grades table. DO NOT scroll, DO NOT click into sub-items. "
    "Just read the ENTIRE table that is visible on the page right now. "
    "Extract every row: the Grade Item name, Points, Weight Achieved, and Grade %. "
    "Do NOT switch courses or click on other courses. Only read THIS course's grades. "
    "Return a parent-ready assessment, not a raw table dump. "
    "For each course, determine: overall status, completed work, missing or zero-score work, "
    "notable low scores, and the single most important next action. "
    "Use the grade rows as evidence, but do not list every row unless needed. "
    "Format exactly like this:\n"
    "COURSE: <course name>\n"
    "STATUS: <on track | behind | concerning | unclear> - <one sentence why>\n"
    "WHAT I SEE: <2-3 concise bullets covering completion, missing work, low scores>\n"
    "NEXT STEP: <one practical action>\n"
    "RAW_EVIDENCE: <compact supporting numbers only, no more than 5 items>\n"
    "Be fast. Read what's on screen and return the assessment."
)


async def create_d2l_session(student_name: str) -> tuple[str, str, str, Any]:
    """Create a cloud browser session + task.

    Returns (task_id, session_id, live_url, task_response) immediately.
    `task_response` is the SDK object — pass it to stream_until_done() to
    receive live step updates via its .stream() method. Discard it if you
    only need the final result (the polling fallback works fine on task_id
    alone).
    """
    # 1. Create a browser session with the D2L profile
    session = await client.sessions.create_session(
        profile_id=BROWSER_USE_PROFILE_ID or None,
    )
    session_id = session.id
    live_url = session.live_url or ""
    log.info("Cloud session %s created, live_url=%s", session_id, live_url)

    # 2. Create a task on that session
    task_text = D2L_TASK_TEMPLATE.format(
        student_name=student_name,
        d2l_url=D2L_URL,
        username=D2L_USERNAME,
        password=D2L_PASSWORD,
    )
    task_response = await client.tasks.create_task(
        task=task_text,
        session_id=session_id,
        llm="claude-sonnet-4-20250514",
    )
    task_id = task_response.id
    log.info("Cloud task %s created on session %s", task_id, session_id)

    return task_id, session_id, live_url, task_response


_RUNNING_STATUSES = {"created", "pending", "queued", "running", "in_progress", "processing"}
_DONE_STATUSES = {"finished", "failed", "stopped", "completed", "done", "success"}


async def stream_until_done(
    task_id: str,
    task_response: Any = None,
    on_step: Optional[Callable] = None,
    timeout: float = BROWSER_TIMEOUT_SECONDS,
) -> str:
    """Stream task steps until completion or timeout. Returns final output.

    If `task_response` is supplied and supports `.stream()`, intermediate
    steps are emitted via `on_step(text)`. Otherwise we fall back to plain
    polling — which still returns the final output, but `on_step` is never
    called (no per-step API on the status endpoint).
    """
    deadline = time.monotonic() + timeout

    async def _final_output_if_done() -> str | None:
        """Return final task output when Browser Use has completed, else None."""
        status = await client.tasks.get_task_status(task_id)
        status_name = str(getattr(status, "status", "") or "").lower()
        if status_name in _DONE_STATUSES:
            if not status.output:
                raise RuntimeError(
                    f"Browser Use task {task_id} ended with status={status.status}, no output."
                )
            return str(status.output)
        return None

    if task_response is not None:
        try:
            async for step in task_response.stream(interval=2):
                final_output = await _final_output_if_done()
                if final_output:
                    return final_output
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Browser Use task {task_id} timed out after {timeout}s"
                    )
                if on_step and getattr(step, "next_goal", None):
                    await on_step(step.next_goal)
        except (AttributeError, TypeError) as e:
            # SDK doesn't expose .stream() on this object — drop to polling.
            log.warning(
                "task_response.stream() unavailable (%s); falling back to polling",
                e,
            )
        else:
            # Stream finished normally — fetch the final output.
            final_output = await _final_output_if_done()
            if final_output:
                return final_output

    # Polling fallback (also reached when .stream() bailed above).
    while time.monotonic() < deadline:
        status = await client.tasks.get_task_status(task_id)
        status_name = str(getattr(status, "status", "") or "").lower()
        if status_name in _DONE_STATUSES:
            if not status.output:
                raise RuntimeError(
                    f"Browser Use task {task_id} ended with status={status.status}, no output."
                )
            return str(status.output)
        if status_name and status_name not in _RUNNING_STATUSES:
            log.info("Browser Use task %s still active with status=%s", task_id, status.status)
        await asyncio.sleep(2)

    raise TimeoutError(f"Browser Use task {task_id} timed out after {timeout}s")


async def check_d2l_grades(student_name: str) -> str:
    """Convenience wrapper: create session + task, run to completion. No live URL exposed."""
    task_id, _, _, task_response = await create_d2l_session(student_name)
    return await stream_until_done(task_id, task_response=task_response)
