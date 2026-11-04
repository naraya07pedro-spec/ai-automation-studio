# Observed CI runtime evidence registry and dump drift · Slice 26

Date: 2026-11-04. **Historical synthetic CI observations only**, not a runtime support matrix for customer systems.

## Evidence snapshot

Source: GitHub Actions [run 41792558426](https://github.com/naraya07pedro-spec/ai-automation-studio/actions/runs/41792558426) of PR #27. Three Ubuntu jobs completed successfully; each executed 267 unit tests and both pinned fixtures matched.

| Python exact patch | SQLite exact patch | OS runner family | Run ID | Observation |
| --- | --- | --- | --- | --- |
| 3.11.17 | 3.45.1 | Ubuntu | 41792558426 | Fixture match, 267 tests |
| 3.12.16 | 3.45.1 | Ubuntu | 41792558426 | Fixture match, 267 tests |
| 3.13.17 | 3.45.1 | Ubuntu | 41792558426 | Fixture match, 267 tests |

The `prototype/lead_inquiry_runtime_evidence.py` registry is explicitly maintained and not auto-expanded. It provides an exact Python/SQLite match lookup and detached, aggregate evidence snapshots. Patches, other SQLite versions or OS families remain unregistered even if the current fixture test passes. No record asserts production support.

## Two distinct questions

1. **Current fixture verification**: Does this interpreter/SQLite combination verify both expected synthetic manifests *now*? The fail-closed `check_runtime_drift()` answers this; any changed digest, failed integrity or missing artifact is a blocker.
2. **Historical observation**: Is this exact runtime tuple in the reviewed registry? `classify_runtime_evidence()` answers it without using historical success to override a current failure. Reports retain `production_supported: false`.

CI prints both reports. The historical registry lookup is informational, not a replacement for the existing strict current drift gate.

## Controlled dump-format drift test

The regression suite simulates a changed logical dump fingerprint by injecting a different fingerprint function into both the low-level backup comparison and disposable recovery verifier. The copies can still agree with each other, but the fixed reference differs, so **both fixture checks must fail**. The stored reference digests remain unchanged, and the gate cannot silently repin them.

These injected Python exceptions and altered functions are synthetic fault tests only, not actual alternate SQLite binary versions or adversarial filesystem concurrency.

## Update procedure

To add evidence, obtain a successful CI run on the exact commit with recorded runner Python/SQLite patch versions, known OS family and complete fixture/test results. Review the relevant job logs, then separately review a registry change. Never infer compatibility for another version merely from a similar version number. A production support decision requires a different set of operational and security evidence.

## Remaining limitations

- Snapshot claims are written into source code by project maintainers, not fetched live or signed by GitHub at runtime; the registry is not an independently authenticated attestation.
- No multiple SQLite builds were tested in the cited matrix: all used SQLite 3.45.1.
- Code with write access can edit the registry and the fixture reference. Version checks do not establish source provenance or data authenticity.
- No real external provider actions, secrets, backup custody, production rollout, historical migration or permanent database architecture selection.
