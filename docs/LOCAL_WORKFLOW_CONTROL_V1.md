# Workflow Revision & Pause Controls v1 · Slice 39

Date: 2026-11-04. Scope: **local deterministic rule workflows**, not authenticated agent deployment, cloud scheduling, or third-party execution.

## Product behavior

An operator can stage **one immutable next-version draft**, inspect its exact service/budget/review rules, publish it explicitly, and **pause or resume** local processing. All actions are initiated through the local browser and validated in the Python core independently of UI state.

### Rules and history

- `POST /api/local-stage` accepts `workflow_id`, exact `expected_version` and the complete new `service_filter`, `min_budget_tier` and `followup_policy`. Only active or paused workflows may stage a new revision. Exactly one draft at a time; up to 30 revisions per workflow.
- `GET /api/local-revisions?workflow_id=...` shows archived, active and pending versions with their allowed rule fields and creation/activation timestamps.
- `POST /api/local-publish` takes `workflow_id`, `expected_version`, and `draft_version`, verifies a real pending draft, and **atomically** moves the existing active version to `ARCHIVED`, activates the new revision and updates the current rule configuration. Old `local_runs` records and their pinned `workflow_version`, decisions and timestamps remain unchanged.
- A running workflow remains `ACTIVE` after publish; a `PAUSED` workflow remains **paused** even after publishing new rules. Publishing alone never resumes it.
- Existing event IDs still replay their originally recorded decisions, even if the current active rules have changed. A changed payload with the same logical ID remains a conflict. New IDs are evaluated with the new rules after publication.

### Pause and resume

- `POST /api/local-pause` and `POST /api/local-resume` require the workflow ID, expected active version and **control epoch**. Any mismatched stale request or invalid state transition fails closed. Each successful transition increases the epoch.
- Paused workflows refuse **new manual events** as well as CSV preview and commit. The historical run ledger and archived rule definitions remain inspectable.
- All local manual-run, batch commit, and state-control operations within the same `LocalWorkflowCore` instance use its shared lock. A run committed just before a pause cannot be retroactively cancelled; the pause blocks subsequent work once applied. No external provider actions exist.
- The CSV snapshot token now binds the `control_epoch` in addition to workflow version, ledger count and file digest. A preview made **before pause** stays invalid after a pause and resume without a new event. Publishing a version also invalidates old snapshots.
- Stage-only work does **not** invalidate active-rule CSV previews because the active version and policy remain unchanged. This is intentional.

### Migration of previously saved data

On startup, the existing SQLite database is **non-destructively upgraded**:
- Adds `local_workflows.control_epoch INTEGER NOT NULL DEFAULT 0`, only if absent.
- Creates `local_workflow_revisions` with immutable versioned rule values and active/draft/archive state.
- Backfills existing workflows into their current revision on first load; existing `local_runs` rows remain unchanged.
- The app does not silently delete or overwrite legacy rule versions or historical results. This migration has a regression test using a hand-built pre-slice-39 SQLite schema containing an existing event.

The registry remains localhost-only and unauthenticated. The data is unencrypted by the app. No customer PII, confidential workflow data, OAuth secrets or provider operations are supported; do not expose it to a public network.

## Explicitly not implemented

No true cancellation of already completed local work, no external send shutdown/reconciliation, no multi-user identity/approval, no editable draft replacement, no rollback-to-older-revision action, no versioned graph editor, no fine-grained permissions, and no production deployment. This first local revision contract intentionally supports **append-only successive versions** to preserve evidence. It does not retroactively reclassify leads or change historic outcomes.

## Acceptance and verification

`tests/test_local_workflow_controls_http.py` adds 23 real HTTP/SQLite acceptance cases for draft/publish, history preservation, pause/resume, stale control epochs, CSV snapshot invalidation, concurrent run/pause, invalid origin and schema migration. `tests_browser/test_real_browser.py` adds three browser scenarios for publishing with old/new event history, pause across reload and publish-while-paused.

Existing 449 Python tests per interpreter and 19 Chromium browser journeys must continue passing. **Expected**, not verified yet: 472 Python cases per Python 3.11/3.12/3.13 and 22 Chromium journeys. Read the actual exact-head CI evidence in `docs/PROGRESS.md` and PR comments before merging.

Next product priority: a controlled, **read-only live integration** with explicit account identity/capability verification, before introducing any external write/send.
