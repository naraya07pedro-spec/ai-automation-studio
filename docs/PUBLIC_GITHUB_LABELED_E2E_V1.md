# Public labeled issue → SQLite E2E acceptance · Slice 42

**Date:** 2026-11-04. **Scope:** One real GitHub API read through the production adapter, untrusted public issue metadata reduced to fixed categorical fields, then an explicitly requested *temporary* SQLite `:memory:` commit. This is **not** production customer intake, authenticated human authorization, or write access to GitHub.

## Actual external acceptance fixture

The project owns and controls this clearly marked **synthetic**, noncustomer GitHub issue:

- [Issue #47: Synthetic fixture for public GitHub Issues read-only intake](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/47).
- Repository: `naraya07pedro-spec/ai-automation-studio`, publicly visible.
- Required labels (verified on the GitHub issue): `aas:service:automation`, `aas:budget:3`, `aas:timeline:soon`.
- Must stay `open` and be a regular issue, **not a pull request**. Contents and labels are invented solely to establish reproducible acceptance evidence.

**One-time fixture setup** was performed with the project owner's linked GitHub connection before CI. Creating the fixture was an explicit external GitHub mutation for test setup, **not a capability of the shipping read-only connector**. The production adapter, CLI probe, and GitHub Actions E2E job have **no GitHub issue write operations**. This issue can remain open as an opt-in public fixture, and should be reviewed/retired deliberately rather than silently deleted.

## Test the exact real issue, not just any conveniently labeled issue

`scripts/probe_public_github_live.py` gained `--expect-issue-number`. The probe computes the same case-normalized, 12-hex-digit repository hash and issue-number-derived event identity as the product, then requires the **specific** numbered issue to be present among sanitized first-page source records. Another eligible issue cannot substitute. Invalid or absent expected issue number fails rather than passing with a generic source.

Example:

```bash
python scripts/probe_public_github_live.py \
  --repo naraya07pedro-spec/ai-automation-studio \
  --expect-issue-number 47 \
  --require-eligible \
  --check-ephemeral-import
```

This command carries out one real, unauthenticated HTTPS GET to `api.github.com`, then:

1. Requires a valid HTTP response (no redirects, custom host, tokens or dangerous URL).
2. Verifies the actual open public issue #47 was on the first page and had **exactly one label in each required category**.
3. Calls the existing app's no-write `PublicGitHubReadOnlyIntake.preview` and stores a short-lived one-time snapshot in process memory.
4. Uses the already-shipped explicit `PublicGitHubReadOnlyIntake.commit` to perform an atomic SQLite `:memory:` import.
5. Inspects the temporary local ledger and asserts the **exact issue-derived event ID, original decision, reason, and workflow version 1** match the preview for every new issue.
6. Returns a small redacted JSON summary and closes the in-memory database. No issue titles, bodies, GitHub identities, raw provider JSON, real customer data, or API credentials are retained or echoed.

**Acceptance status is strictly conditional on an actual CI run.** Passing mocked unit tests alone, a generic successful HTTPS response, or another labeled issue **does not** establish full E2E. The E2E status must match the separate GitHub Actions job result and redacted log for the exact tested commit.

## CI gates

The existing `.github/workflows/public-github-live-smoke.yml` retains its generic transport-only job and adds an independent **Real labeled public issue to ephemeral SQLite** job invoking the strict command above. Both jobs have read-only `contents: read` permissions, checkout with `persist-credentials: false`, and no provider mutation. Other required gates remain the Python 3.11/3.12/3.13 test matrix and Chromium browser suite.

`tests/test_github_public_live_probe.py` adds eight offline regression tests for correct exact-number matching, absent/closed/PR fixture failure, redaction, invalid expected-number rejection before network activity, and ephemeral SQLite parity. They are **not** live-network proof.

The fixture may eventually fall off the API's first 15 open issues, be closed, lose a required label, or be unavailable because of GitHub outage/rate limiting. If any of those conditions occur, the strict job must fail closed. A failed job is an actual finding, not a license to weaken exact-issue verification.

## Explicitly outside scope

No authenticated operator, no private GitHub access, no real sales/customer records, no automatic ingestion, no hosted multiuser service, no external send/write, no model inference, no durable external event queue, and no guarantee of historical issue labels. The read-only adapter is a narrow verified public input path only.

**Next strategic gate:** operator identity and permissions before allowing any real customer data or write-enabled connectors. Do not spend more cycles multiplying fake integration scenarios without progressing toward safe, useful operator controls.
