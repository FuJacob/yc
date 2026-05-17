"""Supermemory wrapper — semantic memory layer for FamilyOps (rfc-7.md).

Best-effort. Every public function:
- No-ops if SUPERMEMORY_API_KEY is empty
- Swallows HTTP errors and timeouts (logs them) so memory failures never break the agent
- Skips when family_id is None (e.g. unknown sender)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from config import (
    MEMORY_RECALL_LIMIT,
    MEMORY_TIMEOUT_SECONDS,
    SUPERMEMORY_API_KEY,
    SUPERMEMORY_BASE_URL,
)

log = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(SUPERMEMORY_API_KEY)


def _container_tag(family_id: int) -> str:
    return f"family_{family_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def remember(
    family_id: Optional[int],
    content: str,
    metadata: Optional[dict[str, Any]] = None,
) -> bool:
    """Create a memory in Supermemory.

    Returns True if stored, False on no-op or error.
    """
    if not _enabled():
        return False
    if family_id is None:
        log.debug("memory.remember: skip (no family_id)")
        return False
    if not content or not content.strip():
        return False

    payload = {
        "containerTag": _container_tag(family_id),
        "memories": [
            {
                "content": content.strip(),
                "metadata": metadata or {},
            }
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=MEMORY_TIMEOUT_SECONDS) as client:
            r = await client.post(
                f"{SUPERMEMORY_BASE_URL}/v4/memories",
                headers={
                    "Authorization": f"Bearer {SUPERMEMORY_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if r.status_code >= 400:
            log.warning(
                "memory.remember failed: %d %s", r.status_code, r.text[:200]
            )
            return False
        log.info(
            "memory.remember stored for family=%s content=%r",
            family_id,
            content[:80],
        )
        return True
    except httpx.HTTPError as e:
        log.warning("memory.remember error: %s", e)
        return False


async def recall(
    family_id: Optional[int],
    query: str,
    limit: int = MEMORY_RECALL_LIMIT,
) -> list[dict]:
    """Semantic search of memories for this family.

    Returns a list of memory dicts (possibly empty). Never raises.
    """
    if not _enabled():
        return []
    if family_id is None:
        return []
    if not query or not query.strip():
        return []

    payload = {
        "q": query.strip(),
        "containerTag": _container_tag(family_id),
        "searchMode": "hybrid",
        "limit": limit,
    }
    try:
        async with httpx.AsyncClient(timeout=MEMORY_TIMEOUT_SECONDS) as client:
            r = await client.post(
                f"{SUPERMEMORY_BASE_URL}/v4/search",
                headers={
                    "Authorization": f"Bearer {SUPERMEMORY_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if r.status_code >= 400:
            log.warning(
                "memory.recall failed: %d %s", r.status_code, r.text[:200]
            )
            return []
        data = r.json()
        results = data.get("results", []) if isinstance(data, dict) else []
        log.info(
            "memory.recall family=%s q=%r returned %d hits",
            family_id,
            query[:60],
            len(results),
        )
        return results
    except httpx.HTTPError as e:
        log.warning("memory.recall error: %s", e)
        return []


async def snapshot_grades(
    family_id: Optional[int],
    kid_name: str,
    grades_text: str,
) -> None:
    """Fire-and-forget: write a dated grades snapshot to memory."""
    if not _enabled() or family_id is None:
        return
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    content = f"{kid_name}'s grades on {date_str}: {grades_text.strip()}"
    metadata = {
        "category": "grades",
        "kid_name": kid_name,
        "source": "d2l_check",
        "snapshot_date": date_str,
    }
    await remember(family_id, content, metadata)


def _extract_text(value: Any) -> str:
    """Return a content string from value (which may be str, dict, or None)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return (
            value.get("content")
            or value.get("memory")
            or value.get("text")
            or ""
        )
    return ""


def _extract_metadata_from(container: Any) -> dict:
    """Return container['metadata'] if it's a dict, else {}."""
    if isinstance(container, dict):
        md = container.get("metadata")
        if isinstance(md, dict):
            return md
    return {}


def _result_to_line(result: Any) -> str:
    """Format one search result for the LLM context block.

    Supermemory returns varying shapes across endpoints:
      {"content": "...", "metadata": {...}}
      {"memory": "...", "metadata": {...}}              ← memory is a string
      {"memory": {"content": "...", "metadata": {...}}}  ← memory is a dict
      {"chunk":  {"content": "..."}, "metadata": {...}}
    We try them all and bail on anything we don't recognize.
    """
    if not isinstance(result, dict):
        return ""

    mem = result.get("memory")
    chunk = result.get("chunk")

    content = (
        _extract_text(result.get("content"))
        or _extract_text(mem)
        or _extract_text(chunk)
    )
    if not content:
        return ""

    # Metadata: check the root first, then peek inside memory/chunk wrappers.
    md = (
        _extract_metadata_from(result)
        or _extract_metadata_from(mem)
        or _extract_metadata_from(chunk)
    )
    category = md.get("category") or "memory"
    return f"- ({category}) {content}".strip()


def format_memories_block(results: list[dict]) -> str:
    """Render memories for the LLM system prompt. Empty string if no results."""
    if not results:
        return ""
    lines = []
    for r in results:
        try:
            line = _result_to_line(r)
        except Exception as e:
            log.warning("memory format error on result %r: %s", r, e)
            continue
        if line:
            lines.append(line)
    if not lines:
        return ""
    return "RELEVANT MEMORIES:\n" + "\n".join(lines)


def fire_and_forget(coro) -> None:
    """Schedule a coroutine without blocking, logging any exception that escapes."""
    task = asyncio.create_task(coro)

    def _on_done(t: asyncio.Task):
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            log.warning("background memory task failed: %s", exc)

    task.add_done_callback(_on_done)
