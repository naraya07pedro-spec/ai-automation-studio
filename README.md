# AI Automation Studio

A **local-only**, interactive engineering demonstration of five deterministic, synthetic business automation workflows. It is **not** a hosted automation SaaS, live AI system or production integration platform.

## Run the demo

Requires Python 3.11+ with SQLite available. No external provider credentials or API billing.

```bash
python app/local_server.py
```

On the same computer, open **http://127.0.0.1:8765**. The server intentionally binds to `127.0.0.1` only. Do not expose it using public proxies or port forwarding.

## Five executable *mock* workflows

| Workflow | Browser interaction | Actual side effects |
| --- | --- | --- |
| Lead Inquiry Qualification | Validate guided mock setup, run synthetic inquiry, inspect trace and review | None |
| Support Request Triage | Route a synthetic support ticket to a mock queue | None |
| Appointment Follow-up | Compare two explicit synthetic timestamps and prepare a reminder plan | None |
| CRM Handoff | Build an allowlisted mock mapped record preview | None |
| Knowledge & Document Routing | Evaluate synthetic document metadata and policy to preview a route | None |

All workflows include input validation, bounded in-memory duplicate/conflict handling, and redacted outcomes. There are no provider sends, real CRM writes, file transfers, AI model calls, stored customer data or persistent user sessions. Claiming local mock review does not authenticate an operator.

## Test the engineering evidence

Run all standard-library backend/HTTP tests:

```bash
python -m unittest discover -s tests -v
```

Optional real Chromium UI acceptance:

```bash
python -m pip install playwright==1.56.0
python -m playwright install chromium
python -m unittest discover -s tests_browser -v
```

PRs run the Python 3.11/3.12/3.13 matrix and a separate Chromium UI job. Chromium tests exercise actual browser interactions against the real loopback server, not mocked API responses. They save synthetic desktop/mobile screenshots as GitHub Actions artifacts.

## Architecture and limitations

- `web/index.html`: responsive browser client.
- `app/local_server.py`: stdlib HTTP server; loopback only and intentionally unauthenticated.
- `prototype/`: deterministic isolated mock workflow engines, state/recovery reference implementation and runtime evidence checks.
- `tests/` and `tests_browser/`: backend HTTP/runtime and real-browser acceptance.
- `docs/PROGRESS.md`: chronological engineering changes and CI evidence.

There is **no** verified public deployment, comprehensive accessibility audit, durable run history, custom builder, 45-scenario catalog, secured production server or real provider adapter in this local demo. The source architecture and CI evidence should be reviewed before relying on anything beyond synthetic sandbox experimentation.
