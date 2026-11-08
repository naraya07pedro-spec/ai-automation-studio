# Scoped Agent memory retention enforcement · owner-explicit purge

This closes a real privacy gap: previously, a scoped note past its configured 7/30/90/365-day expiry could no longer be selected for a plan, but the authenticated list API still exposed its old text. Expiration now blocks both plan selection **and plaintext exposure through the list API**, without deleting unexpectedly.

## What changes

- `GET /api/agent-scoped-memory?project_id=...&agent_id=...` still returns expired note ID, revision, generation, source and expiry for owner inspection and explicit deletion. It **omits the `text` field** when `expired=true`, for plaintext and optional AES-256-GCM stores alike.
- An exact-scope `POST /api/agent-scoped-memory-purge-expired` accepts ONLY `project_id`, `agent_id`, `confirm_expired_purge: "PURGE_EXPIRED_SCOPED_NOTES"` and `current_password`. It requires same-origin single-owner session and CSRF, writes a redacted `SCOPED_AGENT_MEMORY_CONFIG` audit event and refuses malformed or unauthenticated requests. It can purge an Agent's expired notes even after the Agent is disabled.
- All expired rows in the **one selected project + Agent scope** are deleted transactionally by current revision. Active notes in that scope and ALL other scopes are preserved. Repeat purge when already empty is a no-op.
- Owner sees a password-masked purge control in `/workspace` only when the selected scope has expired notes. Confirmation is required. Errors are treated as unconfirmed, with no blind retries.
- Existing plan revision+generation hashes remain safe, and a deleted note cannot resurrect an already approved old plan when recreated with identical content.

## Limits

Expiration alone is not automatic destruction from every physical location. The owner-triggered purge removes expired records **only from the live SQLite ledger**. Previous backups, swap, snapshots and storage remanence may persist; the API explicitly reports `backup_copies_may_remain: true`, `physical_erasure_verified: false`.
It does not purge global legacy memory cards, plan snapshots, transcripts, provider receipts, or other unrelated ledgers. This is not an automatic cleanup job, GDPR compliance certification, secure erasure guarantee or permission to bypass original owner-approved plans.

## Evidence

`tests/test_scoped_memory_retention_purge.py` exercises actual SQLite expiry, authenticated HTTP/CSRF/current owner password, literal confirmation, scope isolation, no expired text in GET, replay no-op and encrypted-mode case when cryptography is installed. All existing Python and browser acceptance must pass on the reviewed head before merge. No actual owner's Windows/SSD or backup restoration was tested.
