# Second executable local mock: Support Request Triage · Slice 30

Date: 2026-11-04. No real support desk, AI model, customer tickets, sending or persistent storage.

## Product result

`Support Request Triage` is now a second **EXECUTABLE_MOCK** entry in the template gallery. The gallery reports two executable mock templates and three explicitly disabled previews.

The interface contains an independent Support Triage panel: synthetic ticket ID, issue category, severity, and customer impact. Submit to `POST /api/mock-triage`, which invokes `prototype/support_triage_flow.py::MockSupportTriage`. The response carries a mock queue, urgency, six-step redacted timeline, deduplication state, `ticket_created: false`, and `external_dispatch_count: 0`. No external integration is reachable via this endpoint.

### Deterministic, inspectable rules

- Categories `access`, `billing`, `technical`, `general` map to distinct named **mock** queues.
- `high` severity, or `normal` severity with `customer_impact=true`, leads to `PRIORITY_REVIEW`; otherwise `STANDARD_REVIEW`.
- Same ticket ID and identical allowed fields returns `SKIPPED_DUPLICATE`. Reusing an ID with different content returns `BLOCKED_CONFLICT`.
- Unknown categories, unexpected keys, arbitrary free text, unsafe IDs and non-boolean impact are rejected **before** consuming deduplication capacity.
- At most 1000 synthetic event identifiers are held per local process. At capacity, new events fail closed.
- Response includes no raw ticket ID or sensitive user-provided text. No actual ticket is created.

## Scope and limitations

This is rule-based simulation, **not AI-generated classification** or real provider work. There is no confidence estimate, verified support identity, external queue, message delivery, durable storage, remote process or authentication. Server remains bound to `127.0.0.1`; do not expose it publicly.

The original Lead Inquiry workflow remains separate and functional. User-facing progress now counts **two of five** intended executable mock workflows, not 2/5 of overall product completeness. Three other cards are clearly previews; the planned 45-scenario catalog is not delivered.

## Running

From repository root: `python app/local_server.py`; visit `http://127.0.0.1:8765`. Choose `Support Request Triage` in the gallery or scroll to the second panel, run a sample, then repeat the same ticket ID to see duplication handling.

## Next product slice

Implement the third end-to-end executable mock template with independently testable state, clear error behavior and inspector UI, while preserving the existing workflows and no external side effects.
