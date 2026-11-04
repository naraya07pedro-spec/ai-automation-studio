# Cross-version synthetic runtime drift matrix · Slice 25

Date: 2026-11-04. **Mock-only runtime regression**, not a product support guarantee.

## CI matrix and fail-closed gate

GitHub Actions `mock-prototype-tests.yml` runs separate matrix jobs for Python **3.11, 3.12, and 3.13** on `ubuntu-latest`, `fail-fast: false`. Each runner records its actual Python/SQLite versions, runs `python tools/check_runtime_drift.py`, validates the synthetic fixture contract, and runs the complete standard-library unittest suite.

`prototype/lead_inquiry_runtime_drift.py` independently runs both fixed synthetic recovery fixtures through their **pinned SHA-256 manifest verifier**. Any mismatch or incomplete/failing restoration returns `RUNTIME_DRIFT_DETECTED`, and the CLI exits with a nonzero status. The code does **not** dynamically update, learn, or repin checksums to make an incompatible runtime pass.

The old `runtime_contract()` version-screen label remains **informational**. It may return `UNTESTED_PYTHON` for 3.12/3.13 even if the actual synthetic fixture test passes. Passing means only these synthetic fixtures matched on the measured runner version.

## Threat model and constraints

- Pinned SQL dump hashes can change across SQLite versions or dumping implementations. An unexpected difference deliberately fails CI. Do not silence failing matrix legs or update hashes without reproducing and reviewing the exact semantic delta.
- Expected digest values are stored locally and are **not cryptographically signed by an independent party**. Code maintainers can modify both fixtures and verifier.
- A green CI matrix across Python minors does not establish universal SQLite builds, operating systems, architectures, crash recovery, filesystem sandboxing or production support.
- All files are created and cleaned within disposable test workspaces. There are no real provider dispatches, historical database migrations, external credentials or retained production backups.

## Upgrade response if a matrix job fails

1. Record failing job, Python and SQLite versions and the failing fixture schema.
2. Confirm whether the failure was an actual dump-format drift, SQLite integrity error, transient infrastructure failure or changed fixture source.
3. Compare fixture semantics using a trusted, reviewed offline reproduction. Do not assume every hash difference is safe or unsafe without evidence.
4. If the stored fixture contract truly changes, review a versioned reference update and regression tests separately. Never automatically repin or bypass the gate.
5. Re-run the entire matrix on the exact new commit SHA before declaring the change compatible.
