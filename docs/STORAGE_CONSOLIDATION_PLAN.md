# Mock storage consolidation decision record · Slice 20

Date: 2026-11-04. **Planning only**. No original database is migrated, deleted, or rewritten; no final persistence engine is selected.

## Compatibility inventory

| Axis | Legacy decision store | Durable event-state store |
| --- | --- | --- |
| Module | `prototype/durable_mock_state.py` | `prototype/lead_inquiry_durable_state.py` |
| Table | `mock_events` | `mock_events` |
| Payload identity | `payload_hash` | `digest` |
| Main status | `NEEDS_REVIEW` / `READY_FOR_REVIEW` | `PENDING` / historical `COMPLETED` |
| Meaning | Classification decision | Reservation and local event lifecycle |
| Safe replay semantics | Duplicate/conflict by payload | Pending requires reconciliation; direct completion disabled |
| Adjacent tables | Not part of its decision-only contract | `mock_pending_reviews`, `mock_review_transitions`, `mock_review_grants` in later slices |

**Non-equivalence:** a decision of `READY_FOR_REVIEW` does not imply that any work completed. Historical `COMPLETED` is a prior local mock state, not independent proof of actual delivery. No automatic conversion between the two categories.

## Candidate consolidation strategies

| Strategy | Advantage | Principal downside | Verdict |
| --- | --- | --- | --- |
| A. Force the legacy table into the event-state schema | One table name | Conflates distinct semantics and risks irreversible historical misclassification | **Reject** |
| B. Keep both schemas permanently | No immediate migration risk | Duplicate maintenance and ambiguous APIs persist | Temporary compatibility only |
| C. Introduce a separate future canonical contract with explicit event and decision concepts | Preserves meaning, allows offline reversible migration | Requires schema and authorization design later | **Preferred planning direction**, not an architecture commitment |

## Release gates before implementation

1. Produce fingerprints, integrity check, and count-only inventory for each synthetic copy. Reject unknown columns/statuses and incompatible existing table names.
2. Prove consistent backup + restore offline under controlled test directories. Block overwrite and report partial artifacts without deleting them automatically.
3. Write an explicit mapping table: `PENDING` stays unresolved, historical `COMPLETED` becomes `LEGACY_COMPLETED_UNVERIFIED` or an equivalent separate archival label, and qualification decisions remain separate.
4. Test same-identity cross-schema collisions, duplicate records, bad digest formats, missing evidence, crashes, and rollback of an offline disposable copy.
5. Define retention, independent approval, verified access privileges, and proof of non-mutation on all originals before any real conversion.
6. Validate a new implementation against both sets of existing regression tests. Remove old writers only after feature and historic compatibility gates pass.
7. Never permit live dispatch or completed-state promotion without verified provider evidence and real operator identity.

## Findings from the slice 20 recovery hardening

The recovery rehearsal now checks **both destination paths before opening an output file**, rejects known symlink parents/targets, and uses exclusive creation with `O_NOFOLLOW` when supported. This reduces unnecessary partial output when a restore target is already present.

These are **not sufficient security boundaries** for hostile concurrent filesystem access: time-of-check/time-of-use races, hardlinks, source marker spoofing, path swaps after validation, and malicious code with direct SQLite access remain. Only isolated test fixtures are supported.

## Recommendation

Keep the two existing stores read-compatible while designing the explicit canonical contract in a later slice. Do not run automatic table rewrites, merge history, or invent an evidence of delivery. Separate business semantics first; choose persistence architecture only after its requirements and threat model are agreed.
