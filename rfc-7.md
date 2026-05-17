# RFC-7: Supermemory Integration

**Status:** **Planned — NOT yet implemented.**
**Author:** Jacob Fu
**Date:** 2026-05-17
**Hackathon:** Call My Agent (AgentPhone @ YC)
**Depends on:** [RFC.md](RFC.md) (base FamilyOps MVP, already shipped)
**Architecture-neutral:** works against either local `browser-use` (RFC.md) or cloud SDK ([RFC-1.md](RFC-1.md)). All hooks are at the orchestrator + tool layer, not the browser layer.

---

## 1. Summary

Add Supermemory as the **semantic memory layer** for FamilyOps. SQLite remains the source of truth for structured family data (who exists, who's verified). Supermemory holds soft, recallable facts that compose across conversations — grade history, kid preferences, recurring rules, past approvals.

**Why now:** sponsor track (Sony WH-1000XM5 headphones), genuinely makes the agent feel personal, and unlocks the killer demo line *"how is Gabe doing in math compared to last time?"* by referencing a remembered grade snapshot.

**Time budget:** 60–75 minutes end to end.

---

## 2. Scope

### In Scope
- A thin `memory.py` wrapper around two Supermemory endpoints (create + search)
- Auto-snapshot grade results into memory after every `check_d2l_grades`
- Auto-recall top-3 relevant memories on every inbound message and inject into LLM context
- Two new LLM tools: `remember_fact(content, category)` and `recall(query)` for explicit stores/reads
- One container tag per family (`family_{id}`) for isolation

### Out of Scope
- Web UI for memory inspection (skip for time)
- Cross-family or organization-wide memories
- File/doc ingest (PDFs, images) — text only
- Memory deletion / forget flow (use Supermemory's TTL via `forgetAfter` if needed)
- Connector branding, scoped keys, profiles API

---

## 3. Architecture Delta

```
   Inbound message
        ↓
  ┌──────────────┐
  │ resolve user │
  │  + family    │
  └───────┬──────┘
          │
          ▼
  ┌────────────────────┐
  │ memory.recall(     │  ← NEW: fetch top-3 relevant memories
  │   message,         │     using the message as the query,
  │   family_tag)      │     containerTag=family_<id>
  └───────┬────────────┘
          │
          ▼
  ┌──────────────────────┐
  │ Orchestrator LLM     │  ← system prompt now includes:
  │  - tools             │     "RELEVANT MEMORIES:\n- ..."
  │  - tools+= remember, │
  │           recall     │
  └───────┬──────────────┘
          │
          ▼
  ┌──────────────────────┐
  │ tool dispatch        │
  │  check_d2l_grades →  │  ← NEW: on success, auto-write
  │   memory.remember(   │     a grade snapshot memory
  │   grades_summary)    │
  └──────────────────────┘
```

Existing tools stay unchanged. Memory layer is additive.

---

## 4. Supermemory API Touchpoints

Two endpoints, both POST. Confirmed against `docs.supermemory.ai/llms.txt`.

### 4.1 Create memory
- `POST https://api.supermemory.ai/v4/memories`
- Headers: `Authorization: Bearer ${SUPERMEMORY_API_KEY}`, `Content-Type: application/json`
- Body:
  ```json
  {
    "containerTag": "family_3",
    "memories": [
      {
        "content": "Gabe's grades on 2026-05-17: CS246 87%, MATH239 92%, STAT230 78%, ENGL109 85%.",
        "metadata": {
          "category": "grades",
          "kid_name": "Gabe",
          "source": "d2l_check"
        }
      }
    ]
  }
  ```

### 4.2 Search memory
- `POST https://api.supermemory.ai/v4/search`
- **⚠ Field-name unknown.** Docs I fetched explicitly showed `containerTag`, `threshold`, and `filters` — **but no free-text query field.** Two viable shapes to try, in order:

  **Option A — top-level query string (best guess):**
  ```json
  {
    "containerTag": "family_3",
    "q": "math grade",
    "threshold": 0.5,
    "limit": 3
  }
  ```

  **Option B — query via `filters` with `string_contains`:**
  ```json
  {
    "containerTag": "family_3",
    "threshold": 0.5,
    "filters": {
      "key": "content",
      "value": "math grade",
      "filterType": "string_contains"
    }
  }
  ```

  Hit `https://api.supermemory.ai/v4/openapi` first (it's the authoritative spec) and pick whichever the schema requires. Budget **15 min** for this verification before writing `memory.py`.

---

## 5. Memory Taxonomy

Container tag: `family_{family_id}` — isolates one family from another.

Categories (in `metadata.category`):

| Category | Examples | When written |
|---|---|---|
| `grades` | "Gabe on 2026-05-17: CS246 87% …" | Auto, after `check_d2l_grades` |
| `school_info` | "Gabe attends Waterloo, uses D2L" | Auto on `register_family`; LLM via `remember_fact` |
| `preference` | "Jacob prefers terse replies"; "Gabe takes AP Calc" | LLM via `remember_fact` |
| `approval` | "Jacob approved TI-84 purchase $118 on 2026-05-17" | (Stripe flow, future) |
| `relationship` | "Gabe has a tutor on Tuesdays at 4pm" | LLM via `remember_fact` |

Every memory always gets `metadata.kid_name` if it's about a specific kid (null otherwise).

---

## 6. File Layout Changes

```
familyops/
  memory.py               # NEW: remember(...) + recall(...)
  tools.py                # +2 tool schemas, +2 dispatch branches
  agent.py                # context builder now includes top-N memories
  config.py               # +SUPERMEMORY_API_KEY
  .env.example            # +SUPERMEMORY_API_KEY=...
```

### `memory.py` shape
```python
async def remember(family_id: int, content: str, metadata: dict) -> dict: ...
async def recall(family_id: int, query: str, limit: int = 3) -> list[dict]: ...
async def snapshot_grades(family_id: int, kid_name: str, grades_text: str) -> None: ...
```

All async, httpx-based, ~80 lines total.

### LLM tools added to `TOOL_SCHEMAS`
```
remember_fact(content, category, kid_name?)   # explicit "remember this"
recall(query)                                  # explicit lookup
```

Most reads happen automatically via the context builder; the LLM only invokes `recall` for follow-ups ("anything else you remember about Gabe?").

---

## 7. Context Injection

In `agent._build_context`, **after** resolving the family, fire `memory.recall(family_id, message_text, limit=3)` in parallel with the DB queries.

**Guards:**
- **Skip recall entirely when sender is unknown** (no `family_id` yet) — first contact from a new parent has no memories to find.
- **Skip the `RELEVANT MEMORIES:` section entirely if recall returns `[]`** — don't dump an empty header into the LLM's system prompt; it just confuses the model into making up memories.
- If `memory.recall` raises or times out, log + continue with no memories. Memory is best-effort, never blocking.

Example context block when memories exist:

```
CONTEXT:
Sender phone: +16139859829
Sender record: name=Jacob, role=parent, state=verified
Their kid: name=Gabe, phone=+17865873754, state=verified
RELEVANT MEMORIES:
- (2026-05-17 grades) Gabe on 2026-05-17: CS246 87% MATH239 92% …
- (preference) Jacob prefers terse SMS replies
- (school_info) Gabe is in 2A CS at University of Waterloo
```

When no memories: just omit the `RELEVANT MEMORIES:` block.

---

## 8. Failure Mode

| Failure | Behavior |
|---|---|
| `SUPERMEMORY_API_KEY` missing | Memory module is a no-op (all calls return success/empty). FamilyOps still works without memory. |
| 5xx from Supermemory | Skip recall, continue with empty memories list. Auto-snapshot retries once async. |
| Network timeout | Same as 5xx. Cap each call at 5s. |

Goal: memory degradation never breaks core flows.

---

## 9. Implementation Steps

| Step | Time | Detail |
|---|---|---|
| 1. Sign up at supermemory.ai, get API key | 5 min | Add to `.env` and `.env.example`. |
| 2. **Verify API shape against OpenAPI** | 15 min | Hit `https://api.supermemory.ai/v4/openapi`. Confirm: create-memory request body, search request body (Option A vs B from §4.2), response shapes. Adjust §4 examples before writing code. |
| 3. Write `memory.py` | 20 min | `remember`, `recall`, `snapshot_grades`. With guards: no-op when `SUPERMEMORY_API_KEY` empty, skip recall when `family_id is None`, return `[]` on any error. |
| 4. Add `SUPERMEMORY_API_KEY` to `config.py` | 2 min | |
| 5. **Bump `MAX_TOOL_CALLS` from 4 → 6** in `config.py` | 1 min | Now 5 LLM-callable tools (was 3); a single message could legitimately call `remember_fact` ×2 + `check_d2l_grades` + final reply = 4 calls plus follow-ups. Headroom matters. |
| 6. Wire `snapshot_grades` into `tools.py::check_d2l_grades` post-success path | 5 min | `asyncio.create_task(snapshot_grades(...))` — fire-and-forget, but **log on task exception** so we notice silent drops (`task.add_done_callback`). |
| 7. Inject memory recall into `agent.py::_build_context` | 10 min | Parallel with DB queries via `asyncio.gather`. Apply guards from §7. |
| 8. Add `remember_fact` + `recall` to `TOOL_SCHEMAS` + dispatcher | 10 min | |
| 9. Smoke test: send a message, register, ask "remember Gabe is in 2A CS", verify recall on next message | 10 min | |
| 10. Demo dry-run (incl. pre-demo grade-check snapshot) | 5 min | |

**Total: ~85 min** (vs. 70 originally — added API-shape verification + tool-cap bump). If you hit 100 min without working recall, ship the LLM tools but skip auto-injection — the demo beat still works as long as memories are being written.

---

## 10. Demo Beat

Insert one moment into the demo script that requires memory to work. **Two prerequisites** (do these before the demo starts):

1. **Run a grade check once during setup** — this writes a snapshot to memory dated today, which the live demo can reference. (Or pre-seed via a direct `memory.remember(...)` call with a dated snapshot.)
2. **Use a Waterloo-realistic course example** consistent with `learn.uwaterloo.ca/d2l/` being a UW deployment. Don't mix high school + university lingo (e.g. "AP Calc" + "MATH 239" is incoherent — AP is US high school, MATH 239 is UW combinatorics).

**Setup line (during onboarding):**
> Parent: "Hey, I'm Jacob, register Gabe at +1… Also remember Gabe is in 2A CS at Waterloo and his tutor is on Tuesdays."

**Payoff (during grade query, after the pre-demo snapshot exists):**
> Parent: "How is Gabe doing in CS?"
> Agent: "Gabe's CS246 is at 87%. Last snapshot had him at 84% — up 3 points. Since he's in 2A CS, that's the right course load for him."

The "Last snapshot had him at 84%" line is what makes judges go *"huh, it actually remembers."* That's the whole sponsor pitch in one sentence.

If you skip the pre-demo grade check, the recall line is fiction and the agent will hallucinate or omit it. Don't skip.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Supermemory API field names differ from our guesses | Verify on first call; OpenAPI spec is at `api.supermemory.ai/v4/openapi`. Adjust before wiring. |
| Adds 200-500ms latency per message | Run recall in parallel with DB lookups. SLA budget already allows for it. |
| LLM gets confused by irrelevant memories | Set `threshold: 0.5` in search; cap `limit: 3`. |
| Auto-snapshot floods memory with duplicates | Snapshot only on `check_d2l_grades` success; include date in content so duplicates are useful (history). |
| API key leak via memory content | We never store passwords or PII (D2L creds live in Chrome profile, not the agent). Memory holds names + grades only. Acceptable for hackathon. |

---

## 12. Rollback

Single env var. Remove `SUPERMEMORY_API_KEY` from `.env`, `memory.py` becomes a no-op, system returns to RFC.md v3 behavior. Zero migration cost — Supermemory is purely additive.

---

## 13. Open Questions

1. **Search request shape** — confirm Option A (`q`) or Option B (`filters.string_contains`) per §4.2 against the OpenAPI spec. Mandatory before writing `memory.py`.
2. Should `register_family` write an initial family-info memory automatically? **Default: yes** — one memory like `"Family: parent=Jacob, kid=Gabe registered 2026-05-17"`. Grounds future recalls.
3. Do we surface memory writes to the user? **Default: no**, unless they explicitly say "remember X" (in which case the LLM should confirm with a one-liner).
4. **Conflict with SQLite truth** — if a recalled memory says "Gabe is pending verification" but SQLite says he's verified, the LLM might get confused. Mitigation: include dates in every memory + system-prompt note that *"SQLite state in CONTEXT is current truth; RELEVANT MEMORIES are historical facts that may be stale."*
