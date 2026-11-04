# Actual-browser QA gate · Slice 34

Date: 2026-11-04. Scope: the local-only, synthetic mock app.

## What this verifies

`.github/workflows/browser-e2e.yml` adds a distinct Chromium job for pull requests and main, alongside the existing three-version Python unit-test matrix. The browser job installs pinned `playwright==1.56.0` and its Chromium runtime, starts the real loopback Python server and uses `tests_browser/test_real_browser.py` to exercise UI controls and real HTTP responses. It does **not** stub backend outcomes.

Eight browser scenarios:
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

## Deliberate limitations

This is automated UI/DOM and mobile-width smoke testing in Chromium only, not a visual quality certification. It does not test Safari/Firefox, real touch devices, screen readers, production identity, CSP hardening, real data, provider integration, persistence, or hosted public security. The server is unauthenticated and **must not** be exposed beyond loopback.

**Next product priority:** Fix any browser-observed defects, explicitly review screenshot artifacts, then design a safe deployable demo without placing the unauthenticated local server on a public network.
