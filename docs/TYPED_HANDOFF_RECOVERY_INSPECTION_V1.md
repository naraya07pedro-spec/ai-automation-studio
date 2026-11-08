# Typed Agent handoff · read-only cross-ledger recovery inspection

When a source Agent's deterministic typed result is copied to a new target-scoped note, the handoff ledger and the scoped memory ledger are separate SQLite transactions. A process interruption after CLAIMED but before/after note creation cannot be blindly retried without risking conflicting or duplicate data.

## New owner-only recovery inspection

The authenticated GET endpoint `/api/agent-typed-handoff-inspect?preview_id=<32 hex>` and `/delegations`'s **Inspect receipt** button now inspect BOTH saved ledgers, with no mutations, local model execution or provider/network requests.

- Inspect the source plan's last-step recorded receipt, immutable plan and security fingerprints, typed deterministic decimal/UTC representation, existing delegation job scope, original target note ID and stored note metadata.
- Compare source-result-derived expected note digest to the currently saved target note's digest, provenance type and label, without echoing the underlying result or target note text in the HTTP response.
- Return cautious classifications such as `CLAIMED_NOTE_ABSENT_REVIEW_NO_RETRY`, `CLAIMED_COMPATIBLE_NOTE_REVIEW_NO_RETRY`, `COMMITTED_RECEIPT_NOTE_COMPATIBLE`, `NOTE_CONFLICT_REVIEW` or `SOURCE_EVIDENCE_CHANGED_REVIEW`. The classification never proves that this specific preview caused the matching note to be written.
- Explicitly return `note_write_provenance_proven:false`, `safe_to_retry_or_auto_repair:false`, and no external dispatch. No method to replay, mutate or resolve `CLAIMED_REVIEW_REQUIRED` was added. Existing target plan is not reapproved, modified, resumed or re-executed.

## Why no automatic repair

A matching note might have been independently created, a later legitimate owner update could have changed the result, or an earlier filesystem backup may not reflect the live ledgers. If any prerequisite evidence or scoped memory is unavailable, inspection fails closed with a generic error rather than declaring the transfer safe. It cannot reconstruct whether a source and target participated in an actual remote provider transaction. This is an **inspection aid**, not authoritative cross-database atomicity.

After an uncertain transfer, preserve the original preview ID, inspect BOTH original ledgers and any independent backups, avoid reusing the same note ID and do not click commit again. If a new transfer is desired, the owner must perform an entirely new reviewed plan/handoff after resolving ambiguity and separate authorization.

Tests exercise real local SQLite source/target plans and queue, synthetic deterministic tool output, a crash immediately before the destination write and after it, mismatched updated notes, denial of anonymous HTTP reads, and no retry permission. No owner Windows data, real Gmail/n8n, external provider charge, or VPS deployment was inspected in CI.
