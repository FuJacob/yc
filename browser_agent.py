import asyncio
import logging

from browser_use import Agent, BrowserSession, ChatBrowserUse

from config import BROWSER_TIMEOUT_SECONDS, CHROME_PROFILE_DIR, D2L_URL

log = logging.getLogger(__name__)


async def check_d2l_grades(student_name: str) -> str:
    """Run a local browser_use Agent against D2L and return a plain-text grade summary."""
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    session = BrowserSession(
        user_data_dir=str(CHROME_PROFILE_DIR),
        headless=False,
        channel="chrome",
    )

    llm = ChatBrowserUse()

    task = (
        f"You are already logged into the University of Waterloo's D2L (Brightspace) "
        f"learning portal as {student_name}. "
        f"Navigate to {D2L_URL}. From the homepage, find and open the Grades section. "
        f"Some D2L deployments require selecting a specific course first; if so, "
        f"visit each current-term course and read the Grades page for each. "
        f"Extract every course code with its current overall grade or percentage. "
        f"Return a plain-text summary, one course per line, in the format: "
        f"'COURSE_CODE: GRADE'. End with a single-line note identifying the "
        f"lowest-performing course. "
        f"Do not close the browser when you finish."
    )

    agent = Agent(
        task=task,
        llm=llm,
        browser_session=session,
        max_failures=3,
        use_vision=True,
        max_actions_per_step=2,
    )

    log.info("Starting Browser Use agent for student=%s", student_name)
    result = await asyncio.wait_for(
        agent.run(max_steps=30),
        timeout=BROWSER_TIMEOUT_SECONDS,
    )

    extracted = result.final_result() if hasattr(result, "final_result") else None
    if not extracted:
        raise RuntimeError(
            "Browser Use did not return a final result. The agent may have "
            "failed to navigate D2L or the session expired."
        )
    return str(extracted)
