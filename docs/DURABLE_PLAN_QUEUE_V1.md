# Stage #115 · Durable cross-plan job orchestration (owner-driven)

Date: 2026-11-07 WIB. **Scope:** local-first, no Tencent VPS, no new paid AI calls, no arbitrary remote tool execution.

## Owner-facing result

This is a meaningful orchestration layer over **existing LocalAgentPlanStore plans**, not a second AI engine. A previously approved plan can contain up to three read-only tools; up to eight such plans can be grouped into a single dependency-aware directed acyclic graph (DAG). The owner visits `/jobs` from Mission Control, creates a job with a label, priority 0–3 and two to eight unique existing approved plan IDs. The UI offers sequential and independent dependency modes, while the backend validates general explicit DAG dependencies, rejecting self-references, missing nodes, duplicates and cycles.

- **Create** creates a durable `DRAFT` record, no tool execution.
- **Arm** validates saved plan fingerprints, original tool approval, and readiness, then transitions to `ACTIVE` without executing any step.
- **Run ONE** requires a separate owner action, fresh password, authenticated session and CSRF. The queue chooses the highest-priority eligible job (fair rotating last-claim order within equal priority), then the first ready plan node in it, and runs at most **one existing read-only plan step**. Per-step model safety shares the same global gate with interactive inference and the existing batch runner.
- **Pause / Resume / Cancel** are durable owner-confirmed state transitions. Pause does not terminate an in-flight step. A job with an active step cannot be paused/cancelled mid-flight; the operator can review the checkpoint afterward.
- **Restart / Crash** converts any persisted `IN_FLIGHT` node and its job into `REVIEW_REQUIRED`; there is no background dispatch on startup and no blind replay.
- **Reconcile** is a conservative owner action for **exactly one** already-persisted plan step. It validates the original plan identity and SHA-256, requires model safety `IDLE`, and checks that `next_index` increased by precisely 1 with the correct number of stored results. Successful reconciliation **pauses** the job. If no durable step is proven, it stays under review instead of guessing and resending.
- **History / Evidence:** job and per-step digests in `durable-plan-jobs.sqlite3`, per-action hash-only audit events in the existing security ledger, no plan prompts or results in queue history. The original plan store remains the authoritative source of tool results.

## Security and operational boundaries

1. Only owner-approved and internally allowlisted **read-only** plan tools may enter the DAG. Model-generated instructions never grant new write permission. No Gmail sends, CRM mutations, webhook dispatches, shell commands, unbounded web access, or arbitrary MCP tools are added by this feature.
2. The queue does **not** execute paid OpenAI proposals: it only executes **already approved local plan steps** through the existing plan store. However, some existing plan step tools may call an installed local model or a separately granted public read source. The label "zero model charges" must not be generalized to other plans or external tool providers.
3. The queue has no background worker. Even with the server powered on, each step requires explicit owner click and fresh password; powering off a local PC stops execution. This is a **durable paused/resumable work queue**, not autonomous 24/7 infrastructure.
4. SHA-256 fingerprints bind original plan goal, model, tool arguments and selected source revision metadata. A changed plan, revoked grant, incomplete dependency, active legacy batch or other conflict fails closed. A digest is an integrity/provenance indicator, not a substitute for authorized and trustworthy source data.
5. A separate SQLite DB is used alongside the existing plan and model-safety DBs. They are **not** one atomic cross-database transaction. Every step first claims `IN_FLIGHT` durably, then uses the existing plan store and safety checkpoint; a crash between commits becomes `REVIEW_REQUIRED`. Do not interpret it as a safe retry.
6. One job/plan node can be enrolled only once. The maximum is 16 jobs, eight plans per job, and 2,000 bounded local events. All queue mutations are owner-password gated and recorded as `DURABLE_JOB_CONTROL` or `DURABLE_JOB_STEP` in the existing security audit.
7. On Linux, the database file and containing private owner directory are restricted to the OS user. On Windows, inherited ACL permissions still require the owner's independent smoke check. The Stage #111 offline encrypted backup enumerates private `*.sqlite3` files under the owner state tree, so **the new database should be included** after a clean shutdown, subject to its size/file limits. Actual owner-state backup and restore remain unverified.

## Operator acceptance

Open `http://127.0.0.1:8765/jobs` after login. Create and approve at least two previously existing local plans using Mission Control section 02 or the supported bounded cloud plan flow. Enter their 24-character IDs in the queue, choose a dependency mode, create a draft, arm it, then explicitly run one step at a time. Verify the queued dependent plan remains untouched until the prerequisite plan becomes COMPLETED, pause/resume once, review event digests, restart cleanly and verify progress persisted. **Do not use real customer data or confidential API keys during trial tests.**

## Reproducible CI

`tests/test_durable_plan_queue.py` covers: approved-plan gating, DAG cycles/duplicates/unknown deps, priority and equal-priority fairness, sequential dependencies, one-step dispatch, pause/resume/cancel, duplicate plan enrollment, plan-source drift, concurrent click/gate protection, simulated process death with no replay, conservative reconciliation and real authenticated loopback API with password/CSRF/audit checks.

`.github/workflows/durable-plan-queue.yml` runs this suite in Python 3.11/3.12/3.13, existing read-only plan, batch and cloud handoff regressions, and Python/JS syntax checks. The broader repo workflows still need to pass on the final PR head before merge.

## Explicit remaining gaps and next recommended work

- Genuine owner-PC runtime acceptance including Windows ACL and offline encrypted backup/restore of the actual new job DB.
- Durable autonomous worker with time-limited owner-approved execution budgets, scheduling policies and accurate post-crash reconciliation across connectors. Not implemented.
- Real n8n downstream outcome verification and safe failure handling, also **not implemented** in this stage.
- Multi-provider routing, bounded long-term memory, machine-verified business outcomes and true 24/7 hosted execution remain separate future goals.

**Do not confuse a green GitHub Actions simulation with a verified production agent.**
