# Local CSV Intake v1 · Slice 38

Date: 2026-11-04. Classification: **functional local, user-initiated file intake**. Not a network integration, autonomous agent, live AI inference, or production data pipeline.

## User outcome

An operator with an **ACTIVE v1 local rule workflow** can select a small CSV file containing invented event IDs and permitted categories, inspect deterministic qualification results **without changing the database**, then explicitly click **Commit verified preview locally**. The resulting events are added to the **same** SQLite event ledger as manual `/api/local-run` submissions. The next browser reload and application restart show the saved results, without storing raw CSV bytes.

The file is limited to these exact headers in this order:

```csv
event_id,service_code,budget_tier,timeline
sample_001,automation,3,soon
sample_002,integration,1,flexible
```

`service_code` is one of `automation`, `integration`, `consulting`; `budget_tier` is `1`, `2` or `3`; `timeline` is `urgent`, `soon` or `flexible`. Event IDs are limited to ASCII letters/digits/underscore/hyphen. A valid-looking ID could still encode personal information. **Do not supply actual customers, names, phone numbers, emails, secrets or confidential records**.

Maximum 25 records and 4 KiB UTF-8/ASCII file contents per batch. Every value is validated server-side using exact allowlists; unknown headers, malformed CSV, duplicate IDs inside the same batch, extra fields and unsupported symbols fail closed. The user interface checks the filename extension and size for usability; the server's schema check is authoritative.

## Server endpoints

- `POST /api/local-batch-preview` JSON `{"workflow_id":"sample_flow","csv_text":"..."}` validates every row and existing event ID before returning a `PREVIEW_READY` report with predicted decisions, reasons, `new_count`, `replay_count`, active workflow version and a preview consistency token. **No write occurs**.
- `POST /api/local-batch-commit` JSON `{"workflow_id":"sample_flow","csv_text":"...","expected_version":1,"preview_token":"..."}` revalidates all rows, confirms the exact version, file digest, and unchanged SQLite run count, then inserts every new event in **one transaction**, returning `BATCH_COMMITTED_LOCAL`.
- Identical already-recorded events appear as `REPLAYED_LOCAL`. Reusing an event ID with altered content causes an entire-batch `409 event_id_payload_conflict`; no partial write.
- A manual event or another committed batch arriving after preview changes the monotonic ledger count. Commit with an old token returns `409 stale_batch_preview`. Re-preview is required.
- Workflow run-capacity checks account for all new events **before** insert. No silent history eviction or background work.
- A `preview_token` is a **consistency digest, not authentication or an approval signature**. This local prototype has no separate authenticated user identity.

## Implementation

- `app/local_csv_batch.py`: bounded stdlib CSV reader; allowlisted parse; exact shared manual/batch fingerprint and `_evaluate_rule`; preflight with duplicate/conflict/capacity checks; transactional commit under the same `LocalWorkflowCore` lock.
- `app/local_workflow_core.py`: extracts the existing deterministic rule function and canonical event fingerprint so CSV and manual paths cannot silently diverge. Existing manual run contract remains unchanged.
- `app/local_server.py`: adds two loopback-only JSON POST endpoints behind existing Host, Origin, MIME, body-size and no-store checks.
- `web/index.html`: local file chooser, separate preview and explicit commit buttons, stale-on-change client gate, redacted feedback and current local-history refresh. The browser reads the file via `File.text()`, never uploads to a third-party host, and sends the CSV only to the same-machine HTTP endpoint. To make reviewing saved runs predictable after a page reload, the selected **workflow ID only** is remembered in the browser tab's `sessionStorage` when available. CSV contents, event payloads, preview tokens and history are not saved in browser storage; SQLite remains the ledger of record.
- `tests/test_local_csv_batch_http.py`: HTTP/SQLite tests for strict content rejection, preview no-write, persistence, atomicity, existing-event replay/conflict, concurrency, stale snapshots and unsafe request origin.
- `tests_browser/test_real_browser.py`: real Chromium end-to-end file attachment, preview and commit; changed-ledger rejection; and invalid extra-column rejection.

## Out-of-scope and next product gates

This is real local manual import but **not live ingestion**. No automatic file watching, external webhook, email, CRM, OAuth, AI provider, multi-user approval, pause/resume, version 2 editing, cross-device synchronization, public network exposure, or verified real-customer data privacy has been implemented.

The app is intentionally localhost-only and unauthenticated, and SQLite is not encrypted by this application. Real customer data must remain out of scope until identity and processing boundaries are implemented and independently tested. Network connectors require explicit security/permission checks and real provider verification.

The next valuable stage is **versioned workflow change management / pause** and a constrained, auditable read-only connector test. This slice itself remains manually triggered, preserving full control of data import.

## Test evidence

GitHub Actions exact-head test results and merge evidence belong in `docs/PROGRESS.md`; don't infer passing status from this specification.
