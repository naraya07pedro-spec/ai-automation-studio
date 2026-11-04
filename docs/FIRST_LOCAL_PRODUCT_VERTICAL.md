# First usable local product vertical · Slice 27

Date: 2026-11-04.

## What exists

`python app/local_server.py` launches a **local-only**, standard-library, account-free product demo at `http://127.0.0.1:8765`.

The `web/index.html` interface offers a guided synthetic Lead Inquiry template, form submission, business outcome, redacted execution trace, duplicate/conflict demonstration and an explicit mock-only label. POST `/api/mock-run` calls the existing `MockInquiryFlow` engine. GET `/api/health` exposes only a mock status. Other routes return 404.

### Run locally

From the repository root:

```shell
python app/local_server.py
```

Open `http://127.0.0.1:8765` in a browser. Run the default event, repeat it to observe duplicate blocking, or reuse the same event ID with a changed field to observe conflict detection.

Run tests:

```shell
python -m unittest discover -s tests -v
```

## Scope and evidence

- No accounts, credentials, provider AI, live CRM, external sending, payment, hosting, or persistence.
- Qualification is deterministic presence checking of explicitly provided synthetic fields, **not AI inference**.
- The HTTP server is bound only to IPv4 loopback and uses process-local deduplication. Its register resets on restart. The shared flow is protected by a process-local lock for concurrent requests.
- Request bodies are capped at 8192 bytes; only four allowed synthetic string fields are accepted. No raw inbound values appear in routine JSON responses or HTTP access logs. Browser outputs use `textContent`, not untrusted HTML insertion.
- This is a development demonstration with no authentication or CSRF token, not a production network service. It must **not** be exposed via public proxy, network port forwarding or deployment to an internet-facing address. It is not a hardened browser security model.
- `READY_FOR_REVIEW` indicates a local mock classification only; no message/CRM operation is executed or verified.
- The demo does not yet include a general template catalog, custom builder, persistent run history, human approval UI, live connectors, AI model calls or recruitment-ready public deployment.

## Next product-driven slice

Add an explicit in-browser execution timeline and draft-follow-up preview using a non-sending mock plan. Then build a bounded template gallery and guided setup while keeping all side effects disabled.
