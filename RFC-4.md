# RFC-3: Kid-Initiated Payment Requests via Sponge

**Status:** Draft
**Date:** 2026-05-17
**Depends on:** RFC.md (core FamilyOps MVP), RFC-1.md (optional progress UX patterns)
**Primary goal:** A verified kid can request payment for a specific service, and a verified parent can approve or decline before any money moves through Sponge.

---

## 1. Summary

FamilyOps currently proves the "one family phone number" pattern with onboarding, kid verification, and grade checks. RFC-3 extends that same message-driven workflow to payments:

1. Kid texts FamilyOps: "Can you pay $12 for Quizlet Plus?"
2. FamilyOps extracts the service, amount, and reason, converts the amount to integer cents, then creates a pending payment request.
3. Parent receives a deterministic approval prompt with a short request code.
4. Parent replies `APPROVE 482193` or `DECLINE 482193`.
5. If approved, FamilyOps executes the payment through Sponge when the target is machine-payable, or starts a Sponge browser-checkout/card approval flow when it is a normal merchant checkout.
6. Kid and parent both receive final status updates.

The core rule: **the LLM may create or summarize a request, but it must never execute spend without a persisted parent approval.**

---

## 2. Product Contract

### In Scope

- Verified kids can request payment for one specific service, merchant, or paid endpoint.
- Verified parents can approve or decline via text.
- Requests include amount, currency, service name, reason, and optional service URL/payment URL.
- Parent approval is idempotent and tied to a 6-digit request code.
- Sponge handles the actual payment surface:
  - x402/MPP paid endpoints through Sponge Wallet helpers.
  - A hardcoded Sponge payment link for the Phase 3 demo path.
  - Browser checkout / virtual card as a follow-on path for normal merchant websites.
- Payment actions are logged in an append-only audit table.
- No raw card number, expiry, or CVC is logged, stored, or sent over AgentPhone.

### Out of Scope for First Implementation

- Recurring subscriptions.
- Partial approvals or parent-edited amounts.
- Multiple parents approving the same request.
- Refund handling.
- Chargeback handling.
- Kid-held Sponge keys.
- Arbitrary browser shopping without a URL, amount cap, and parent approval.
- Letting the LLM choose a different merchant, service tier, or amount after approval.

---

## 3. Sponge Findings

Relevant Sponge primitives from the docs and starter repo:

| Need | Sponge surface | Notes |
|---|---|---|
| Connect one agent wallet | `SpongeWallet.connect(api_key=...)` | Python SDK supports agent-scoped wallet actions with `SPONGE_API_KEY`. |
| Backend-provision wallets | `SpongePlatform.connect(...)`, `platform.createAgent(...)` | Master-key control plane can create/manage many agent wallets and spending limits. |
| Pay agent-ready APIs | `wallet.paid_fetch`, `wallet.x402_fetch`, `wallet.mpp_fetch` | Best fit for API/service payments where the target supports x402 or MPP. |
| Pay a normal checkout | Browser checkout + saved payment method or virtual card | Sponge creates approval before charge and returns checkout-scoped credentials after approval. |
| Accept/request payment | Payment links | Payment links are single-use links with status checks; useful if the kid/service supplies a Sponge link or if we later let FamilyOps collect money. |
| Direct low-level access | `paysponge_openapi` / `HttpClient` | Use only when a payment-link endpoint is not wrapped by the Python SDK yet. |

Sources:

- Sponge docs index: https://docs.paysponge.com/llms.txt
- Sponge Wallet overview: https://docs.paysponge.com/
- SDK: https://docs.paysponge.com/wallet/sdk.md
- Python examples: https://docs.paysponge.com/wallet/python-examples.md
- Payments and cards SDK: https://docs.paysponge.com/wallet/sdk-payments-cards.md
- Browser checkout: https://docs.paysponge.com/wallet/browser-checkout.md
- Use your own card: https://docs.paysponge.com/wallet/user-cards.md
- Platform SDK: https://docs.paysponge.com/wallet/sdk-platform.md
- Starter repo: https://github.com/paysponge/sdk-starter

---

## 4. Architecture

```
Kid ──iMsg/SMS──┐
                │
Parent ─iMsg/SMS┼──> AgentPhone webhook
                │         │
                │         ▼
                │   FastAPI /webhook
                │         │
                │         ▼
                │   Orchestrator LLM
                │   - extracts request details
                │   - routes approve/decline
                │   - never spends directly
                │         │
                ▼         ▼
          SQLite state + audit
          - users/families
          - payment_requests
          - payment_events
                │
                ▼
          Sponge client wrapper
          - wallet connect
          - payment execution
          - status polling
          - no card logging
```

The payment state machine lives in our backend, not in the prompt. Sponge is called only from deterministic tool handlers after DB validation.

---

## 5. Recommended MVP Shape

Build the first pass as an approval ledger plus one Sponge execution path.

**Decision:** Phase 3 uses a Sponge payment link as the only automatic payment target. Hardcode one demo payment-link target/service in config or seed data. Do not decide between x402, MPP, browser checkout, and payment links live during the demo.

### Preferred Demo Path

Use a service that is payable through a known Sponge payment link. This avoids arbitrary browser checkout risk and lets the demo stay inside a controlled API flow.

Example:

```
Kid → bot:     "Can you pay $2 for the research service? I need it for homework."

bot → Parent:  "Alex wants $2.00 for research service:
                'I need it for homework.'
                Reply APPROVE 482193 or DECLINE 482193."

Parent → bot:  "approve 482193"

bot:           validates parent, validates status, marks approved
bot:           calls Sponge payment execution

bot → Parent:  "Approved. Paid $2.00 for research service."
bot → Kid:     "Approved — the payment for research service went through."
```

### Why Not Start With Full Browser Checkout?

Sponge supports browser checkout, but it is a higher-risk second milestone:

- The agent must navigate the merchant checkout reliably.
- Parent approval in FamilyOps is not the same as Sponge's checkout/card approval.
- Card credentials must be requested only when the merchant form is ready.
- Raw card details must never be logged, shown, or sent through chat.

For RFC-3, browser checkout is designed as Phase 5, not the first demo path.

---

## 6. Data Model

Add three tables. Keep the existing `families` and `users` tables.

### `payment_requests`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Internal id |
| `family_id` | INTEGER FK | Same family as kid/parent |
| `kid_user_id` | INTEGER FK | Requesting kid |
| `parent_user_id` | INTEGER FK nullable | Approving parent; nullable until resolved |
| `request_code` | TEXT | 6-digit human code, e.g. `482193`; unique per family while active |
| `service_name` | TEXT | "Quizlet Plus", "Perplexity research service" |
| `service_url` | TEXT nullable | URL when supplied |
| `merchant_name` | TEXT nullable | Optional normalized merchant |
| `description` | TEXT | Kid's reason / request text |
| `amount_cents` | INTEGER | Exact approved cap; all internal payment logic uses cents, never floats |
| `currency` | TEXT | Default `USD` |
| `payment_kind` | TEXT | `x402`, `mpp`, `payment_link`, `browser_checkout`, `manual` |
| `payment_target` | TEXT nullable | URL, link id, endpoint id, or merchant URL |
| `status` | TEXT | See state machine below |
| `sponge_reference` | TEXT nullable | tx hash, session id, payment link id, or checkout id |
| `failure_reason` | TEXT nullable | User-safe error summary |
| `expires_at` | TEXT | Approval expiration |
| `created_at` | TEXT | ISO timestamp |
| `updated_at` | TEXT | ISO timestamp |

### `payment_events`

Append-only audit log.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `payment_request_id` | INTEGER FK | |
| `actor_user_id` | INTEGER nullable | Kid, parent, or null for system |
| `event_type` | TEXT | `created`, `parent_notified`, `approved`, `declined`, `expired`, `executing`, `paid`, `failed` |
| `message` | TEXT | User-safe audit note |
| `metadata_json` | TEXT | No secrets, no card data |
| `created_at` | TEXT | |

### `sponge_wallets`

MVP wallet ownership is represented by this table. `.env` may still hold the demo `SPONGE_API_KEY`, but the DB row is the source of truth for which family/parent owns the payment surface.

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `family_id` | INTEGER FK | |
| `owner_user_id` | INTEGER FK | Parent owner |
| `sponge_agent_id` | TEXT nullable | Sponge agent id |
| `api_key_ref` | TEXT nullable | Reference to secret store, not raw key if avoidable |
| `mode` | TEXT | `demo_env`, `agent_key`, `platform_managed` |
| `status` | TEXT | `active`, `disabled` |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

Constraints:

- Add a partial unique index for active codes, e.g. unique `(family_id, request_code)` where status is one of `draft`, `pending_parent`, `approved`, or `executing`.
- `expired` and `declined` are terminal states reached without execution; `paid` and `failed` are terminal states after execution.
- One active wallet per family for MVP.
- The parent owns the Sponge wallet/payment method; kids never hold Sponge keys.
- In `demo_env` mode, `api_key_ref` can be `SPONGE_API_KEY` and the actual key stays in `.env`.

---

## 7. Payment State Machine

```
draft
  │ enough details captured
  ▼
pending_parent
  │ parent declines                 │ parent approves
  ▼                                 ▼
declined                         approved
                                    │ execution starts
                                    ▼
                                executing
                                 /    \
                                ▼      ▼
                              paid   failed

pending_parent ── expired (lazy, on approve attempt past expires_at)
```

Rules:

- Only a verified kid can create a request.
- Only a verified parent in the same family can approve or decline.
- `approve` is valid only from `pending_parent`.
- Payment execution is valid only from `approved`.
- `executing` must be entered in a DB transaction before calling Sponge to prevent duplicate spends.
- AgentPhone retry or duplicate parent texts must not execute twice.
- Request codes are 6-digit random numeric strings and must be unique for active requests within the same family.
- Requests expire after a short window, default 30 minutes. Expiration is enforced lazily: `approve_payment_request` checks `now > expires_at` before any state transition; if expired, mark the request `expired`, notify both users, and return without approving.

---

## 8. LLM Tool Surface

The orchestrator receives these additional tools. All tools must enforce role/family/status rules internally.

| Tool | Caller | Purpose | Side effects |
|---|---|---|---|
| `create_payment_request(service_name, amount_cents, currency, reason, service_url?, payment_kind?)` | Kid | Create a pending request and notify parent | DB insert, parent text |
| `approve_payment_request(request_code)` | Parent | Approve a pending request | DB transition to `approved`, start payment execution |
| `decline_payment_request(request_code)` | Parent | Decline a pending request | DB transition, kid notification |
| `get_payment_request_status(request_code)` | Kid or parent | Read status | None |

Prompt additions:

- If the kid omits amount or service, ask one short follow-up.
- Tool schemas use `amount_cents` as an integer. The LLM may parse "$2", "2 dollars", or "2.00" from the kid's message, but the tool argument must be integer cents, e.g. `200`.
- `amount_cents` must be a positive integer.
- All user-facing dollar strings are formatted from `amount_cents` and `currency`; never store or pass a float amount separately.
- If the kid gives a service without a payment target, create a `manual` request and tell the parent this needs manual fulfillment unless a URL/link is provided.
- If the parent replies "approve" and exactly one request is pending, allow the tool call with that request code; otherwise ask which code.
- Never call payment execution tools from a kid message.
- Never change amount, merchant, or payment target after parent approval.

---

## 9. Sponge Integration Design

Add `sponge_client.py` as a thin wrapper. Keep it lazy-imported so Sponge setup issues do not break onboarding or grades.

```python
from config import SPONGE_API_KEY, SPONGE_API_URL

def _wallet():
    from paysponge import SpongeWallet

    kwargs = {"api_key": SPONGE_API_KEY}
    if SPONGE_API_URL:
        kwargs["base_url"] = SPONGE_API_URL
    return SpongeWallet.connect(**kwargs)

def get_sponge_balances() -> dict:
    return _wallet().get_balances()

def pay_x402(*, url: str, method: str = "GET", body: dict | None = None) -> object:
    return _wallet().x402_fetch(
        url=url,
        method=method,
        preferred_chain="base",
        body=body,
    )

def pay_mpp(*, url: str, method: str = "POST", body: dict | None = None) -> object:
    return _wallet().mpp_fetch(
        url=url,
        method=method,
        body=body,
    )
```

Exact method signatures should be verified against the installed `paysponge` version. The starter repo uses `paysponge>=0.1.0`, `SPONGE_API_KEY`, optional `SPONGE_API_URL`, and guarded transfer examples.

For Phase 3, hardcode the first executable route to `payment_link` only. `x402`, `mpp`, and `browser_checkout` stay present in the model for future work, but they must return "not implemented for this demo" until the payment-link path is proven.

### Payment Execution Routing

`execute_approved_payment(request_id)`:

1. Load request in a DB transaction.
2. Verify `status='approved'`.
3. Set `status='executing'`.
4. Append `executing` event.
5. Dispatch by `payment_kind`:
   - `payment_link`: use the hardcoded demo Sponge payment-link API/status flow.
   - `x402`: future path; do not execute in Phase 3.
   - `mpp`: future path; do not execute in Phase 3.
   - `browser_checkout`: enqueue browser checkout flow; do not request card credentials until merchant form is ready.
   - `manual`: no automatic spend; parent gets manual instructions.
6. Store Sponge reference / result metadata without secrets.
7. Set `paid` or `failed`.
8. Notify parent and kid.

### Config

Add to `.env.example`:

```
# Sponge
SPONGE_API_KEY=sponge_live_xxx
SPONGE_MASTER_KEY=sponge_master_xxx
# Optional. Defaults to hosted Sponge Wallet API.
# SPONGE_API_URL=https://api.wallet.paysponge.com

# Payment policy
PAYMENT_REQUEST_TTL_MINUTES=30
PAYMENT_DEFAULT_CHAIN=base

# Phase 3 demo target
PAYMENT_DEMO_KIND=payment_link
PAYMENT_DEMO_SERVICE_NAME=research service
PAYMENT_DEMO_TARGET=pl_demo_or_url
```

`SPONGE_MASTER_KEY` is not required for the first demo if using one pre-created wallet. Use it later for per-family wallet provisioning and fleet spending limits.

---

## 10. User Flows

### 10.1 Kid Creates a Request

```
Kid → bot:      "Can you pay $15 for the math tutoring practice pack?"

                LLM sees verified kid. Extracts:
                service_name="math tutoring practice pack"
                amount_cents=1500
                reason="kid requested it"
                payment_kind="manual" unless URL/link supplied.
                Calls create_payment_request(...).

bot → Parent:   "Alex wants $15.00 for math tutoring practice pack.
                 Reply APPROVE 482193 or DECLINE 482193."

bot → Kid:      "I asked Jacob. I'll tell you when they approve or decline."
```

### 10.2 Parent Approves

```
Parent → bot:   "approve 482193"

                Tool validates:
                - parent is verified
                - request belongs to same family
                - status is pending_parent
                - request has not expired

                Tool marks approved and starts execution.

bot → Parent:   "Approved. Paying $15.00 for math tutoring practice pack now."
bot → Kid:      "Approved — I'm working on the payment now."
```

### 10.3 Parent Declines

```
Parent → bot:   "decline 482193"

bot → Parent:   "Declined."
bot → Kid:      "Jacob declined the math tutoring practice pack request."
```

### 10.4 Payment Succeeds

```
bot → Parent:   "Paid $15.00 for math tutoring practice pack. Ref: spg_..."
bot → Kid:      "Payment went through."
```

### 10.5 Payment Fails

```
bot → Parent:   "Approved, but Sponge could not complete the payment: insufficient USDC balance."
bot → Kid:      "The payment was approved, but it did not go through. Jacob has the details."
```

---

## 11. Policy and Safety

### Hard Rules

- A kid cannot approve their own request.
- The parent must approve a specific code, not a generic "sure" unless only one request is pending.
- Amount parsing happens once at request creation. The stored `amount_cents` is the approved and executed amount.
- The amount executed must match the approved amount.
- Payment execution must be idempotent.
- Never send or log card credentials.
- Never store raw Sponge keys in SQLite.
- Do not let recent conversation history override DB state.

### Approval Policy

For hackathon/demo:

- Parent can approve only one request per text.
- Requests expire after 30 minutes.
- No configurable amount cap for the hackathon; demo spend is controlled by the hardcoded payment-link target and parent text approval.

For production:

- Amount caps.
- Parent-configurable per-kid daily/weekly limits.
- Merchant allowlists/blocklists.
- Category rules.
- Separate "ask me every time" vs "auto-approve under $X" modes.

---

## 12. Files Changed

| File | Change |
|---|---|
| `requirements.txt` | Add `paysponge` |
| `config.py` | Add Sponge env vars and payment policy constants |
| `db.py` | Add payment tables + helpers |
| `agent.py` | Update system prompt with payment routing rules |
| `tools.py` | Add payment tool schemas and dispatch |
| `sponge_client.py` | New Sponge wrapper; lazy imports; no card logging |
| `payment_service.py` | New deterministic state machine and execution logic |
| `.env.example` | Add Sponge and payment policy env vars |
| `README.md` | Add setup/demo runbook for payment requests |

Implementation note: do not import Browser Use from payment code. RFC-3 should be runnable even if D2L/browser automation is disabled.

---

## 13. Implementation Phases

### Phase 0 — Stabilize Backend Imports

- Move Browser Use imports inside `check_d2l_grades()` or behind the grade tool branch.
- Verify `python -c "import main"` succeeds.
- This prevents the Sponge/payment flow from being blocked by browser automation.

### Phase 1 — Payment Ledger Only

- Add DB schema and helpers.
- Add `create_payment_request`, `approve_payment_request`, `decline_payment_request`.
- Tool input and DB storage use `amount_cents` integer everywhere.
- Request code generation uses random 6-digit codes, unique per active family request.
- No Sponge spend yet.
- Done when kid request → parent approve/decline → kid notification works.

### Phase 2 — Sponge Readiness

- Add `paysponge`.
- Add `sponge_client.py`.
- Load `SPONGE_API_KEY`.
- Add a CLI/smoke command to print current agent and balances.
- Done when backend can connect to Sponge and read balances without spending.

### Phase 3 — Controlled Payment Execution

- Implement one execution path only: hardcoded Sponge payment link.
- Require parent approval before execution.
- Add idempotency and status transitions.
- Done when one approved request results in one Sponge payment and both parties get status.

### Phase 4 — Policy Layer

- Add amount caps.
- Add expiration job/check.
- Add duplicate webhook protection.
- Add request-code disambiguation.
- Add audit log viewer/debug command.

### Phase 5 — Browser Checkout

- Add normal merchant checkout only after Phase 3 is solid.
- Require merchant URL, item/service, and max spend.
- Use Sponge browser checkout/card APIs.
- Never display/log/store card credentials.
- Report success/failure back to Sponge and to both users.

---

## 14. Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Duplicate AgentPhone webhook or repeated parent approval spends twice | Critical | DB transaction: only `approved -> executing` once. For the hackathon, rely on this DB transition; add Sponge idempotency later only if the payment-link endpoint exposes it cleanly. |
| LLM produces ambiguous or float amount | High | Tool schema accepts only `amount_cents` integer; reject zero/negative/non-integer values before creating a request. |
| Request code collision | Medium | Generate 6-digit random code and retry insert on same-family active-code collision. |
| LLM routes kid text to approval tool | Critical | Tool validates caller role and family; LLM cannot bypass server checks. |
| Parent approves ambiguous request | High | Require request code unless exactly one pending request exists. |
| Amount/service changes after approval | High | Store immutable approved fields; execution uses DB, not new LLM args. |
| Sponge payment succeeds but notification fails | Medium | Payment status remains `paid`; notification can be retried. |
| Sponge payment status is unknown | Medium | Store `executing`, poll/check status, expose manual reconciliation. |
| Insufficient funds | Medium | Preflight balance for wallet payments; return clean failure to parent. |
| Browser checkout leaks card credentials | Critical | Never log card responses; card credentials are entered only into checkout form; prefer payment links/x402 first. |
| Kid abuses requests | Medium | Rate-limit per kid; parent caps; require verified family. |
| Regulatory/compliance concerns around minors and payments | High | Parent owns Sponge wallet/payment method; kid only creates requests. |
| AgentPhone outage blocks approval UX | High | Keep request ledger; allow manual local admin approval for demos. |

---

## 15. Demo Script

**0:00 — Setup.** "FamilyOps is already connected to a parent-owned Sponge wallet."

**0:10 — Kid request.**

Kid texts:

```
can you pay $2 for the research service? I need it for homework
```

**0:25 — Parent approval.**

Parent receives:

```
Alex wants $2.00 for research service. Reply APPROVE 482193 or DECLINE 482193.
```

Parent replies:

```
approve 482193
```

**0:40 — Sponge execution.**

Backend marks the request `executing`, calls Sponge, then records the result.

**1:00 — Close.**

Parent and kid both receive success or failure. Show the SQLite audit row / Sponge reference.

---

## 16. Decisions

Answered decisions:

- First demo target: hardcoded Sponge payment link.
- Wallet ownership: parent-owned family wallet tracked in `sponge_wallets`.
- Request code format: 6-digit random numeric code, unique per family among active requests.
- Amount format: `amount_cents` integer everywhere after LLM extraction.
- Approval channel: AgentPhone text only for the hackathon. No separate Sponge approval step.
- Payment-link idempotency: rely on our DB status transition for the hackathon; add Sponge idempotency only if the exact endpoint exposes it cleanly.
- Demo cap: no configurable cap for the hackathon.

Recommendation: build the ledger and approval tools independent of Sponge first, then plug in only the hardcoded payment-link path.
