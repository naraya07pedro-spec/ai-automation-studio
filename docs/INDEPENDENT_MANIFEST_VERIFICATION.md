# Independent synthetic manifest verification · Slice 23

Date: 2026-11-04. Offline, fixed-fixture, mock-only verification.

## Why compare against an independent reference?

Slices 19-22 established local SQLite backup/restore equality, aggregate counts,
and disposable workspace cleanup. However, source, backup, and restore could
all be identically corrupted or consistently modified and still compare equal.

`prototype/lead_inquiry_manifest_verifier.py` now holds **pinned reference
SHA-256 logical SQLite dumps and exact aggregate counts for each of two known
synthetic fixture schemas**. It deliberately does not import the fixture
creator, open SQLite, or call external services.

`run_disposable_recovery()` checks this independent reference *before*
returning `DISPOSABLE_REHEARSAL_PASSED`. An internally consistent but changed
fixture yields `FIXTURE_REFERENCE_MISMATCH` with no success manifest.

## Strict verification contract

The verifier `verify_fixed_fixture_manifest(report)` rejects:
- unrecognized schemas, report shapes, extra fields, missing artifacts;
- non-success statuses, missing cleanup flags or any claimed external dispatch;
- inconsistent schema, pinned aggregate counts, or logical fingerprints;
- boolean values where exact integers are required;
- failed artifact integrity, invalid hash notation and new manifest versions.

It returns only a sanitized boolean and status label, never event identifiers,
payloads, file paths, exception messages, or user-supplied values.

## Concurrent operation

Tests launch 20 recovery rehearsals across two schema variants in a thread
pool, plus a mixed batch with unsupported schema requests. Each successful
call owns a separate temporary workspace and must independently pass the
reference check. These tests exercise in-process concurrency, **not**
distributed locks, hostile multi-process access, or power-failure durability.

## Trust limits

Pinned local hashes defend against accidental fixture drift or code regressions.
They are **not** independently signed attestations or proof of a source's
real-world origin. Any code maintainer able to edit both the verifier and the
fixture generator can modify their definitions together. The SQL dump
fingerprint may change with SQLite dump-format variation across versions;
therefore supported runtime versions must be documented before using this
as a stable cross-platform compatibility guarantee.

No actual customer data, live connector effects, historical migration,
provider receipts, persistent backup custody, or final architecture choice
is in scope.

See `docs/DISPOSABLE_RECOVERY_WORKSPACE.md` and
`docs/STORAGE_CONSOLIDATION_PLAN.md`.
