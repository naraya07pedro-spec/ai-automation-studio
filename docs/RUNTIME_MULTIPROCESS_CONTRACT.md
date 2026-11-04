# Python/SQLite runtime and multi-process mock recovery contract · Slice 24

Date: 2026-11-04. This documents **CI and disposable synthetic fixture coverage**, not broad product/platform compatibility.

## Runtime baseline

- CI uses `ubuntu-latest` and `actions/setup-python@v5` with **Python 3.11**. The runner's underlying SQLite version can vary with its Python build; the workflow now prints both versions in a dedicated compatibility-baseline step.
- `prototype/lead_inquiry_runtime_contract.py::runtime_contract()` returns sanitized Python and SQLite versions, plus `BASELINE_ELIGIBLE_NOT_PROVEN` when running Python 3.11 and SQLite >= 3.35.0. A different Python minor version returns `UNTESTED_PYTHON` and an older SQLite on 3.11 returns `SQLITE_BELOW_TEST_BASELINE`.
- **The 3.35 screen is a conservative test policy, not an empirically proven minimum requirement or vendor support statement.** It does not establish correctness. Actual fixed-fixture fingerprint verification in CI determines compatibility with the recorded fixtures.
- Expected SHA-256 values depend on SQLite's logical dump format. They are pinned in a module separate from the fixture producer. No cross-platform/multiple-version compatibility matrix has been completed.

## Separate Python processes

`tests/test_lead_inquiry_runtime_multiprocess.py` uses `subprocess.run` with the same Python executable in isolated import mode (`-I`), fixed worker code, bounded timeout and each worker's own temporary SQLite workspace.

The tests:
- generate and independently verify event-state and legacy-decision manifests across OS process boundaries;
- execute eight recoveries concurrently across separate interpreter processes, checking the manifest again in the parent process;
- mix invalid schema requests with valid workers and ensure valid workers are not contaminated;
- require redacted reports and no retained workspaces or external dispatch.

These checks are not distributed workload tests, hostile interprocess locks, crash/power-failure survival or real sandboxing. The subprocess interpreter is the same Python build on the same CI runner, **not a cross-version matrix**.

## Limitations / next gates

- Fingerprint equality and `integrity_check` do not establish real-world data provenance, delivery, encryption or recovery custody.
- `runtime_contract()` is informational and not an authentication boundary, OS sandbox, or promise of SQLite portability. The full fixture-manifest gate is still mandatory.
- Interpreter processes do not share Python memory, but they do share the runner's kernel/filesystem and can run concurrently; this does not prove multi-host isolation.
- The pathful recovery helper remains accessible in a test context. No production dataset, network action, historical migration, or permanent database architecture is involved.

Recommended next slice: run explicit Python/SQLite version compatibility matrix where available and register evidence of actual hashes; reject unsupported build drifts with a documented upgrade procedure rather than silently repinning checksums.
