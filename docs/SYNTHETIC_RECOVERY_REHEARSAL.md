# Offline synthetic backup and restore rehearsal · Slice 19

Date: 2026-11-04. This is an **offline mock-only verification**, not a general-purpose backup tool.

## Approved scope

`prototype/lead_inquiry_synthetic_recovery.py::rehearse_synthetic_backup_restore(source, backup, restored)` accepts an existing SQLite mock database **only** when a fixture table `mock_synthetic_fixture_marker` contains exactly the marker `AI_AUTOMATION_STUDIO_SYNTHETIC_V1`. It requires one of the two recognized mock schemas from the slice-18 inventory.

The function uses an SQLite read-only source connection and the SQLite online backup API to create separate backup and restored database files. It refuses to overwrite existing destinations, checks SQLite `PRAGMA integrity_check` on original/backup/restore, compares the logical SQL dumps of backup and restored copy by SHA-256, and checks the restored schema and aggregate counts.

Only status, schema name, aggregate count, and match result are returned. The original database is not modified by this code, and historical `COMPLETED` remains historical `COMPLETED`, **not** evidence of external delivery. Decision-only `READY_FOR_REVIEW` remains distinct.

## Limitations that matter

- The marker is self-asserted inside the file. **It is not cryptographic provenance.** Anyone with file write permission can add it. Never use this on real customer data or arbitrary files.
- The code does not authenticate operator identity, validate off-host backup custody, encrypt outputs, protect against hostile processes or symlink races, prove retention compliance, or test recovery after disk/power failure.
- Backups/restores are intentionally left on disk after success; partial artifacts may remain after failures. Never run without an isolated, disposable workspace.
- A source can change through another writer during inspection. SQLite backup provides a consistent backup snapshot, but the returned report does **not** prove that its contents match source state at every point in time.
- Hash comparison covers the logical backup vs restored copy, not an independent externally authenticated expected state. Hashes are not a substitute for signed provenance.
- SQLite `integrity_check` checks database structure but does not prove the business meaning of records.
- This slice does not migrate schemas, select a final database, make real network calls or dispatch work.

## Verification and next gates

Run `python -m unittest discover -s tests -v` and the synthetic fixture validator in CI. Inspect test failures before claiming success. Before any real migration: use independent secured backups, restoration under an operator-approved procedure, explicit data ownership and retention controls, verified access boundaries, workload quiescence, and a rollback rehearsal.

See `docs/LEGACY_MIGRATION_GATE.md` and `docs/MOCK_MUTATION_AUDIT.md`.


## Slice 20 hardening note

The rehearsal now checks both destination paths before creating any output. An existing backup or restore path returns `DESTINATION_EXISTS`; resolved alias collisions return `PATH_COLLISION`. Symlink targets and symlinked parent directories are rejected; exclusive file creation uses `O_NOFOLLOW` where available.

This reduces accidental partial artifacts and obvious path aliases. **It does not eliminate TOCTOU races, hard-link aliases, malicious source-marker injection, external writers or privilege bypass.** Keep the tool inside an isolated test workspace and do not treat it as a secure production backup utility.

See `docs/STORAGE_CONSOLIDATION_PLAN.md` for the non-destructive compatibility strategy.


## Slice 21: failure-injection and hard-link isolation tests

The rehearsal rejects synthetic source files whose hard-link count differs from one, along with source paths beneath symlinked parent directories. Four internal test-only checkpoints permit deterministic `OSError` simulation immediately after backup reservation, backup copy, restore reservation, and restore copy.

Expected outcomes: a failure returns `REHEARSAL_FAILED` rather than a false success; source fixture bytes remain unchanged; partial output artifacts are retained, inspected separately and protected from overwrite on retry.

**Scope warning:** Tests do not simulate sudden process termination, persistent hardware corruption or race-free security against hostile filesystem actors. This is only bounded regression evidence for disposable synthetic fixtures.
