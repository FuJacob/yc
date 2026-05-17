import asyncio
import logging
import time
from typing import Callable, Optional

from browser_use_sdk import AsyncBrowserUse

from config import BROWSER_TIMEOUT_SECONDS, BROWSER_USE_API_KEY, BROWSER_USE_PROFILE_ID, D2L_URL

log = logging.getLogger(__name__)

client = AsyncBrowserUse(api_key=BROWSER_USE_API_KEY)

D2L_TASK_TEMPLATE = (
    "You are already logged into the University of Waterloo's D2L (Brightspace) "
    "learning portal as {student_name}. "
    "Navigate to {d2l_url}. From the homepage, find and open the Grades section. "
    "Some D2L deployments require selecting a specific course first; if so, "
    "visit each current-term course and read the Grades page for each. "
    "Extract every course code with its current overall grade or percentage. "
    "Return a plain-text summary, one course per line, in the format: "
    "'COURSE_CODE: GRADE'. End with a single-line note identifying the "
    "lowest-performing course."
)


async def create_d2l_session(student_name: str) -> tuple[str, str, str]:
    """Create a cloud browser session + task.

    Returns (task_id, session_id, live_url) immediately.
    The task starts running in the background on Browser Use's cloud.
    """
    # 1. Create a browser session with the D2L profile
    session = await client.sessions.create_session(
        profile_id=BROWSER_USE_PROFILE_ID or None,
    )
    session_id = session.id
    live_url = session.live_url or ""
    log.info("Cloud session %s created, live_url=%s", session_id, live_url)

    # 2. Create a task on that session
    task_text = D2L_TASK_TEMPLATE.format(student_name=student_name, d2l_url=D2L_URL)
    task_response = await client.tasks.create_task(
        task=task_text,
        session_id=session_id,
        llm="claude-sonnet-4-20250514",
    )
    task_id = task_response.id
    log.info("Cloud task %s created on session %s", task_id, session_id)

    return task_id, session_id, live_url


async def stream_until_done(
    task_id: str,
    task_response=None,
    on_step: Optional[Callable] = None,
    timeout: float = BROWSER_TIMEOUT_SECONDS,
) -> str:
    """Stream task steps until completion or timeout. Returns final output."""
    deadline = time.monotonic() + timeout

    if task_response is not None:
        # Use the SDK's built-in streaming
        async for step in task_response.stream(interval=2):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Browser Use task {task_id} timed out after {timeout}s")
            if on_step and step.next_goal:
                await on_step(step.next_goal)

        # After stream ends, get final result
        status = await client.tasks.get_task_status(task_id)
        if not status.output:
            raise RuntimeError(
                f"Browser Use task {task_id} ended with status={status.status}, no output."
            )
        return str(status.output)

    # Fallback: manual polling
    while time.monotonic() < deadline:
        status = await client.tasks.get_task_status(task_id)
        if status.status in ("finished", "failed", "stopped"):
            if not status.output:
                raise RuntimeError(
                    f"Browser Use task {task_id} ended with status={status.status}, no output."
                )
            return str(status.output)
        await asyncio.sleep(2)

    raise TimeoutError(f"Browser Use task {task_id} timed out after {timeout}s")


async def check_d2l_grades(student_name: str) -> str:
    """Convenience wrapper: create session + task, poll to completion. No live URL exposed."""
    task_id, _, _ = await create_d2l_session(student_name)
    return await stream_until_done(task_id)
