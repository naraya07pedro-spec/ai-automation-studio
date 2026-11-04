# Async setup and stale-result hardening · Slice 35

Date: 2026-11-04. Scope: local synthetic Lead Inquiry UI only.

## Failure mode
A prior setup validation request could return `DRAFT_VALID` after a user changed source, destination, policy, timezone or budget. The old success response then reenabled Run for a new configuration that had never been validated in the browser. An in-flight Lead Inquiry response could also populate an obsolete draft/review state or reenable Run in its `finally` block after a form edit.

Backend `/api/mock-run` already independently revalidates submitted configuration. Thus the specific observed flaw is browser consistency and misleading authorization UI, not proof that a live action was sent; the application never dispatches externally.

## Correction
- Increment one monotonic `setupRevision` on every setup edit or validation submission.
- Snapshot the setup payload at validation submission, and ignore a response whose revision no longer matches current form state.
- Associate each Lead Inquiry run with its setup revision. Ignore obsolete run responses after an edit and never resurrect a stale review token.
- Keep Run disabled unless the *current* setup is verified and the gallery contract has loaded.
- Invalidate visible draft text and local review controls when setup changes.

## Regression verification
`tests_browser/test_real_browser.py` now includes two adversarial real-browser network-latency tests, in addition to the eight earlier actual Chromium journeys. A scoped browser `fetch` wrapper forwards to the **real local Python server**, then delays delivery of its response until the test edits form controls. No fabricated positive API result is injected.

Checks:
1. A late `DRAFT_VALID` for an old setup cannot reenable Run after source changes.
2. A late real `/api/mock-run` response cannot reenable Run or restore old review controls after destination changes.

Acceptance gate: existing Python 3.11/3.12/3.13 tests plus all Chromium browser tests pass on the **exact** PR head. Test counts are reported only after job logs are inspected.

## Remaining limits
- Stale browser responses are discarded, **not cancelled server-side**. A submitted synthetic event may still update process-local dedup, so this safeguard is not transactional cancellation.
- A local-only, unauthenticated demo. No trusted identity, real provider dispatch, durable approvals, cross-process revocation, live policy authorization or hosted security boundary.
- Other four independent mock forms do not yet use a shared request-versioning component. Extend it after proving the pattern with tests, rather than applying a large unreviewed refactor.
