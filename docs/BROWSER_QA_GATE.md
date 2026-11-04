# Actual-browser QA gate · Slice 34

Date: 2026-11-04. Scope: the local-only, synthetic mock app.

## What this verifies

`.github/workflows/browser-e2e.yml` adds a distinct Chromium job for pull requests and main, alongside the existing three-version Python unit-test matrix. The browser job installs pinned `playwright==1.56.0` and its Chromium runtime, starts the real loopback Python server and uses `tests_browser/test_real_browser.py` to exercise UI controls and real HTTP responses. It does **not** stub backend outcomes.

Original eight browser scenarios (Slice 34):
- Five executable gallery cards can navigate to the correct form without external website links.
- Lead Intake setup is disabled before validation, then a real Lead Inquiry response renders its server-side seven-stage timeline and fixed draft; local acknowledgement and duplicate response are visible.
- Changing setup invalidates the ability to run without revalidation.
- Support Triage routing and duplicate handling render in the browser.
- Appointment Follow-up renders its synthetic 24-hour plan and zero-message outcome.
- CRM Handoff blocks unacknowledged review and displays mapped records only after the local mock acknowledgement.
- Knowledge & Document Routing blocks a restricted label and prepares only a public metadata route preview.
- Phone-width Chromium viewport (375px) can use the fifth form and must not have horizontal document overflow.

The suite writes **synthetic** full-page screenshots of the initial desktop gallery and the mobile 375px view to `browser-artifacts/`; CI uploads them for a separate human visual review. Automated screenshots by themselves do **not** constitute an expert visual or accessibility approval.

## Local run

Install Playwright and Chromium on a development workstation:

```sh
python -m pip install playwright==1.56.0
python -m playwright install chromium
python -m unittest discover -s tests_browser -v
```

The script starts and stops `app/local_server.py` on localhost port 8765. That port must be unused. Tests require a Chromium binary, unlike the stdlib-only Python unit tests.

## Later browser race regressions (Slices 35–36)

The full browser suite now contains **14 Chromium journeys**:
- Slice 35: two delayed genuine localhost response tests for Lead Inquiry setup validation and setup edits during an in-flight run. These detect stale setup and obsolete review UI updates.
- Slice 36: four delayed genuine localhost response tests for Support Triage, Appointment Follow-up, CRM Handoff, and Knowledge Routing. Each runs a newer synthetic event before delivering the original older HTTP result, and verifies that the earlier success cannot overwrite the newer UI state.

Latest test evidence before this documentation update:
- [Browser run 41820163455](https://github.com/naraya07pedro-spec/ai-automation-studio/actions/runs/41820163455): **14 tests passed** in Chromium.
- [Python run 41820163434](https://github.com/naraya07pedro-spec/ai-automation-studio/actions/runs/41820163434): **402 Python tests each** on 3.11, 3.12, and 3.13; no failures.

This documentation change itself creates a new PR head and **requires a fresh exact-head CI pass**. Output screenshots do not replace independent visual accessibility and visual-design audit.

## Deliberate limitations

This is automated UI/DOM and mobile-width smoke testing in Chromium only, not a visual quality certification. It does not test Safari/Firefox, real touch devices, screen readers, production identity, CSP hardening, real data, provider integration, persistence, or hosted public security. The server is unauthenticated and **must not** be exposed beyond loopback.

**Next product priority:** Audit Lead Inquiry event-input and review-response races, independently examine screenshot artifacts, and design a safe deployable demo without exposing the unauthenticated loopback server.
