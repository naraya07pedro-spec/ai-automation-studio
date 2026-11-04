# Async response consistency for secondary mock workflows · Slice 36

Date: 2026-11-04. Scope: browser presentation of the four local synthetic workflow demos, not production connectors.

## Bug and impact

Support Triage, Appointment Follow-up, CRM Handoff and Knowledge Routing previously updated browser output unconditionally when a pending HTTP request finished. Editing an input while the request was in flight could display an old successful result as though it belonged to the latest input. A late error could also replace a newer success or restore controls at the wrong time. Previously rendered records and routes also stayed visible after the user changed the form. The Python mock backend already applies schema and dedup checks; the defect was **browser presentation consistency**, not evidence of an external dispatch.

## Implementation

A shared `workflowGuard` in `web/index.html` creates an independent monotonically increasing revision for each of the four forms. Every input/change event invalidates the displayed result, clears stale timeline/record previews, resets mock external-effect counters to 0, and makes the form runnable. Each submission takes a new revision and clears previous outputs. Success and failure handlers check the captured revision **after the real HTTP response and JSON parsing**. Only the current request may render results, errors or update the button in `finally`.

Concurrent synthetic requests may both reach the loopback server. A user who edits a record but reuses its event ID may receive the backend's existing duplicate/conflict verdict. The UI guard does not alter that policy.

## Real browser regression acceptance

`tests_browser/test_real_browser.py` adds four Chromium tests, one per workflow. Each intercepts the browser's `fetch` after forwarding to the real local HTTP server, holds the *delivery* of the first genuine response, edits a form field, verifies that stale output was removed, changes the synthetic event ID and executes a fresh run. The tests then release the delayed first response and assert that it cannot overwrite the newer result or reactivate the wrong button. Every scenario verifies zero external-effect counters, with record and route preview assertions where applicable.

These four tests supplement the ten existing browser journeys. Backend coverage remains the pre-existing 402 Python unit tests per supported interpreter; the browser tests require Playwright/Chromium in CI.

## Limits and next steps

- Browser revision guards cannot cancel server work already processed. Here that work is synthetic, process-local, and has no provider sends.
- No durable user identity, approval, external CRM/document/calendar operation, AI provider, persistent queue, production hosting, or safe publicly accessible server was introduced.
- This slice does not claim cross-tab state synchronization, accessibility certification, Safari/Firefox compatibility or verification of live production traffic.
- Follow-on work should cover the Lead Inquiry's event-input and in-flight local review race as a separate acceptance target, then conduct actual visual review and safe public-demo design.

Verification: inspect GitHub Actions exact-head browser and Python CI before merge. Record actual results in `docs/PROGRESS.md`; do not infer success from code review alone.
