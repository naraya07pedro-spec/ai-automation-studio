# Local workflow rule core v1 · Slice 37

Date: 2026-11-04. Classification: **functional local rules and SQLite history**, not an AI agent or an internet automation server.

## Concrete user capability

From the local browser an operator can:

1. Create an account-free **private rule workflow draft** with a short workflow ID, a nonpersonal label, a service category filter, a minimum budget tier (1–3), and an explicit next-action policy.
2. **Activate** immutable workflow version 1 by supplying the expected version. A missing draft, wrong version or duplicate activation fails closed.
3. Evaluate a manually supplied pseudonymous event with an explicit category, budget tier and urgency. The server, not the browser, decides qualification and reason.
4. See the outcome and **retrieve durable run history** stored transactionally in SQLite. Restarting the Python server does not erase the local rule definitions or run history.
5. See a previously processed event return `REPLAYED_LOCAL` with its original recorded result, while a reused event ID with changed input fails `event_id_payload_conflict`.

This is distinct from the five existing `EXECUTABLE_MOCK` examples. It is a *genuinely executing local deterministic rule engine*, but **not** live AI inference, integrated source ingestion, sending, CRM writes, scheduler, or a production-ready product.

## Actual endpoints

- `POST /api/local-create` creates an immutable v1 `DRAFT`; limits 50 workflows.
- `POST /api/local-activate` requires an exact `workflow_id` and `expected_version`; no external action is enabled.
- `POST /api/local-run` executes v1 rules, commits one event identity and reason code, limits 500 entries per workflow, and never silently evicts prior evidence.
- `GET /api/local-workflows` lists local configuration/status and run counts.
- `GET /api/local-history?workflow_id=...` returns the 25 latest entries and exact count.

`app/local_workflow_core.py` uses Python standard-library SQLite, strict field allowlists, prepared SQL parameters, transactional inserts, per-instance lock, and hashed payload fingerprint. No raw free-text lead messages or external source data is accepted.

`app/local_server.py` keeps the loopback-only bind and checks Host and Origin for the local-core HTTP surface. `create_handler()` uses an isolated in-memory store by default so existing tests do not touch disk. Running `python app/local_server.py` uses `~/.ai-automation-studio/local-workflows.sqlite3` for persistent, explicitly submitted local-rule data. The five mock workflows continue to use ephemeral in-memory stores.

**Caution:** Loopback Host/Origin checks are not authentication. Other local programs running under the user's account may be able to access the database or service. This release is not suitable for real customer PII, confidential records or unattended externally reachable deployment. Never tunnel or proxy the development server publicly. The SQLite file is not encrypted by the application. User must protect their OS profile and storage.

## Functional rule semantics (not AI)

- Mismatched service: `NOT_QUALIFIED_LOCAL / SERVICE_MISMATCH`.
- Budget below configured minimum: `NOT_QUALIFIED_LOCAL / BUDGET_BELOW_THRESHOLD`.
- Flexible/unknown timeline: `NEEDS_REVIEW_LOCAL / TIMELINE_UNSPECIFIED`.
- Otherwise review policy: `NEEDS_REVIEW_LOCAL / POLICY_REQUIRES_REVIEW`.
- Otherwise draft-only: `QUALIFIED_DRAFT_ONLY / MATCHED_LOCAL_RULES`.

No draft email is sent, no authenticated approval is recorded, and there is no provider outcome to reconcile. Each result reports `external_dispatch_count: 0`.

## Verification and product limitations

`tests/test_local_workflow_core_http.py` tests HTTP+SQLite persistence (including reopening a DB connection), version gate, deterministic decisions, duplicate vs conflict, adversarial input, 500-entry bounded-store gate, origin/host constraints and concurrency. Two real Chromium browser tests cover create → activate → evaluate → saved history → reload and duplicate/payload conflict. Existing tests remain untouched.

This first functional slice intentionally does **not** implement workflow editing, pause/resume, durable worker queues, connector execution, live OAuth, multi-user roles, AI model calls, custom workflow graphs, external webhooks, history deletion/export or server crash recovery during external sends. Those are separate future releases with explicit security and behavior gates.

Priority after this slice: evaluate a safe **read-only connector** or an explicit, non-PII import path to feed the local core from real inputs, then add versioned edits and activation rollback without breaking run identity or history. Do not label an unverified connector `LIVE_VERIFIED`.

**CI rule:** This document is not test evidence. Test passing status must be checked on the exact PR head in GitHub Actions before merge.
