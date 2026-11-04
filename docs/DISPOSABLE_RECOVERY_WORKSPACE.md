# Disposable synthetic recovery workspace · Slice 22

Date: 2026-11-04. **Local, synthetic-only demonstration**, not a backup product, migration mechanism, or secure sandbox.

## API and behavior

`prototype/lead_inquiry_disposable_recovery.py::run_disposable_recovery(schema="EVENT_STATE_V1")` offers one supported argument: the allowlisted synthetic schema name, `EVENT_STATE_V1` or `LEGACY_DECISION_V1`.

The routine creates a fresh `TemporaryDirectory`, constructs fixed synthetic SQLite fixture rows and a fixture marker, performs the existing SQLite backup/restore rehearsal, validates each artifact separately, and returns **an aggregate manifest only after successful verification**. The temporary directory is removed on exit even when an ordinary handled failure happens.

Manifest: schema label, counts for each recognized state, SHA-256 fingerprint of the **logical SQL dump** for source/backup/restore, per-artifact `integrity = ok`, `source_unchanged`, and `all_artifacts_verified`. No raw event IDs, arbitrary filesystem paths, secret values, or provider evidence are returned.

The contract compares logical content and source bytes before and after. It is **not an independently signed checksum**, and no external backup artifact remains after this rehearsal. Nothing is persisted as a usable recovery backup.

## Failure handling

Unsupported schema is rejected without allocating a directory. Recovery failures return `RECOVERY_FAILED`, altered artifacts return `ARTIFACT_MISMATCH`, and expected workspace/SQLite operational failures return `WORKSPACE_FAILED`. Failure responses contain no manifest. Programmer assertions are not silently converted into success.

Tests cover both schema variants, deterministic manifests across fresh workspaces, path-parameter rejection, cleanup after success/failure, artifact mismatch, secret redaction, and zero external dispatch.

## Remaining limits

- TemporaryDirectory is a convenience isolation mechanism, **not a hardened filesystem sandbox**. It does not prevent privileged processes, mount tricks, arbitrary Python import access or malicious OS-level races.
- The marker and test fixture generation are defined in code. They are not cryptographic proof of data origin.
- Temporary artifacts vanish at the end, so this mode does not prove off-host custody, durability, encryption, backup retention, recovery under power loss, or operational restore readiness.
- SHA-256 of a known synthetic fixture is deterministic and checkable but not independent proof of real-world state. Artifact counts and integrity checks only validate the bounded synthetic rehearsal.
- No original historical data, real customer data, connector dispatch, completed-state promotion or database architecture change occurs.

See `docs/SYNTHETIC_RECOVERY_REHEARSAL.md`, `docs/STORAGE_CONSOLIDATION_PLAN.md`, and `docs/MOCK_MUTATION_AUDIT.md`.
