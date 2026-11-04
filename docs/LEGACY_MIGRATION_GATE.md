# Legacy mock storage compatibility and migration gate · Slice 18

Date: 2026-11-04. This is a **no-migration** slice.

## Two incompatible schemas

| Experimental module | `mock_events` columns | Allowed stored values | Semantics |
| --- | --- | --- | --- |
| `prototype/lead_inquiry_durable_state.py` | `source,event_id,digest,state` | `PENDING`, `COMPLETED` | Event reservation and historical local completion |
| `prototype/durable_mock_state.py` | `source,event_id,payload_hash,decision` | `NEEDS_REVIEW`, `READY_FOR_REVIEW` | Cached mock classification decision, **not** event completion |

The two implementations use the same table name but incompatible columns.
Do not open the same file with the other implementation's writable constructor.
Never map `READY_FOR_REVIEW` to `COMPLETED`: it says nothing about delivered work.

## Safe inspection contract

`prototype/lead_inquiry_legacy_inventory.py::inventory_mock_sqlite(path)`:
- Opens an **existing, regular, non-symlink file** with SQLite `mode=ro`; never creates or modifies the database.
- Recognizes only exact event-state-v1 and legacy-decision-v1 column sets.
- Returns only schema labels and counts by recognized status, never identifiers, payloads, digests, SQLite errors or absolute paths.
- Rejects unexpected columns, unknown values, missing tables, corrupt files or unsupported paths without attempting recovery or a schema rewrite.
- Does **not** perform a migration. Tests inspect synthetic files only.

## Future migration prerequisite checklist

1. Confirm who owns the historical file and its intended schema. Stop all writers and reconcile in-flight reservations before even considering conversion.
2. Create an independently verified, encrypted backup using a separate process and prove restoration works; never treat a live file copy as a proven consistent backup.
3. Record source schema, row counts, reference hashes of backup artifacts, integrity-check result, retention/legal requirements and operator approvals. Do not publish raw customer/event IDs.
4. Define a target data contract with explicit `LEGACY_COMPLETED_UNVERIFIED` (or equivalent) and a separate decision-status field. The legacy `COMPLETED` value must **not** imply provider delivery.
5. Define deterministic mapping for every known state and a quarantine path for ambiguous/inconsistent rows. Never silently discard or auto-replay `PENDING`.
6. Require a dry-run migration on an offline disposable copy, counts and constraints reconciliation, idempotency tests, conflict simulation, rollback rehearsal, and independent review before a production run.
7. Perform approved migration only after the real authorization/evidence and storage/access boundaries are selected. No deployment or final database architecture is authorized in this slice.

## Remaining risks

SQLite read-only mode prevents SQL writes through **this inventory connection**, not modifications by other processes with file access. Concurrent writers can change counts after inspection. Recognition by column names is only a compatibility screen, **not** proof of data authenticity, schema integrity or migration readiness.

Relevant source: `docs/MOCK_MUTATION_AUDIT.md`.
