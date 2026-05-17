"""Map Browser Use step events to short, spoken-friendly phrases.

The voice agent's model polls a grade fetch repeatedly and reads each returned
phrase aloud. We need short (≤12 words), TTS-friendly text — no jargon, no
URLs, no markdown.

Two entry points:

- `summarize_step(raw_step)` — best-effort summarization of a Browser Use step
  message into a phrase. Returns None to skip (filler will be used instead).
- `rotating_filler()` — generator of generic fillers used when no specific
  step is available. Wired through `_pump_browser_use_into_state` so the
  caller never hands the voice model an empty queue without a fallback.
"""

import itertools
import logging
import re
from typing import Iterator, Optional

log = logging.getLogger(__name__)


# Course code: 2–4 uppercase letters then a space-optional 2–3 digit number
_COURSE_RE = re.compile(r"\b([A-Z]{2,4})\s?(\d{2,4})\b")
_GRADE_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*%")

# Generic fillers cycled when no specific step is available. Mix of pacing
# ("still going") and progress ("almost there") so the parent doesn't hear
# the same word twice in a row.
_FILLERS = [
    "Still going.",
    "Almost there.",
    "Looking up the grades.",
    "One moment.",
    "Reading the page.",
    "Hang on.",
    "Pulling the numbers.",
    "Nearly done.",
]


def rotating_filler() -> Iterator[str]:
    """Infinite generator that cycles filler phrases."""
    return itertools.cycle(_FILLERS)


def _spell_course(code: str) -> str:
    """Pronounce 'CS246' as 'C S 246' so TTS doesn't say 'CS' as a word."""
    letters, digits = re.match(r"([A-Z]+)(\d+)", code).groups()
    return " ".join(letters) + " " + digits


def summarize_step(raw_step: str) -> Optional[str]:
    """Return a short spoken phrase, or None to skip.

    Best-effort. We don't know the exact shape of Browser Use step messages
    at write time, so we pattern-match on common substrings.
    """
    if not raw_step:
        return None

    text = raw_step.strip()

    # If the step has a course code + a grade-like number, that's our gold case.
    course_match = _COURSE_RE.search(text)
    grade_match = _GRADE_RE.search(text)
    if course_match and grade_match:
        course = _spell_course(course_match.group(1) + course_match.group(2))
        grade = grade_match.group(1).rstrip(".0") or grade_match.group(1)
        return f"{course}, {grade} percent."

    # Course code without a grade — "looking at CS246"
    if course_match:
        course = _spell_course(course_match.group(1) + course_match.group(2))
        return f"Looking at {course}."

    # Heuristics on keywords. Cheap. Tune in Phase 3 against real step output.
    lower = text.lower()
    if "login" in lower or "logged in" in lower or "signed in" in lower:
        return "Signed in."
    if "grade" in lower and ("page" in lower or "section" in lower or "open" in lower):
        return "Opening the grades page."
    if "course" in lower and ("list" in lower or "select" in lower or "found" in lower):
        return "Looking at the course list."
    if "navigat" in lower or "going to" in lower or "load" in lower:
        return "Opening D 2 L."
    if "extract" in lower or "reading" in lower or "scrap" in lower:
        return "Reading the grades."

    # Anything else we don't recognize: skip.
    return None
