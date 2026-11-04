# Template Gallery and Guided Mock Setup · Slice 29

Date: 2026-11-04. **Local-only mock product UI**, no real AI, external integrations, messaging or customer data persistence.

## Gallery

The local homepage now renders a catalog fetched from `GET /api/templates`. There are **five displayed cards**: one `EXECUTABLE_MOCK` template, `Lead Inquiry Qualification`, plus four honest `PREVIEW_NOT_EXECUTABLE` concepts (CRM Handoff, Support Request Triage, Appointment Follow-up, Knowledge & Document Routing). Preview buttons are disabled and cannot activate a backend flow. These are not yet the planned complete catalog of 45 scenarios or five fully executable workflows.

## Guided Setup

A guided setup form for Lead Inquiry includes selected mock source (`mock_inbox` or `mock_web_form`), mock destination (`mock_crm` or `mock_sheet`), follow-up policy (`approval_required` or `draft_only`), timezone (`Asia/Jakarta` or `UTC`), and a zero-to-one-million IDR **synthetic** budget setting. AI mode is pinned to `mock`; no user credentials or API keys are accepted.

- `GET /api/setup-choices` exposes server-authorized choices, not arbitrary user-defined connectors.
- `POST /api/mock-setup` validates exact fields with the existing `validate_configuration()` and returns an aggregate verdict (`DRAFT_VALID` or `BLOCKED`), no user data or credentials.
- The UI disables workflow execution until an explicit successful setup-validation request. Editing the form invalidates this state and requires another validation.
- `POST /api/mock-run` accepts the previous four-field payload for backward-compatible tests and an optional validated `template_id` + `setup` pair for the guided form. It independently validates every new request again, rather than trusting the frontend gate. Unknown or preview templates, unexpected setup keys, out-of-bounds budgets, live destinations or non-mock AI modes are rejected before reserving the event.
- `draft_only` creates a fixed local never-sent draft with **no review token**; `approval_required` retains the pre-existing one-use local acknowledgement token. The timeline reports the selected policy accurately.

## Security and scope boundaries

These are development-only allowlisted mock adapters. The mock budget is stored only as an input configuration and **does not implement a production cost-governance engine**. The server remains unauthenticated and bound to loopback, so it must not be exposed on LAN, reverse-proxy, or public web hosting.

In-memory deduplication and local review tokens reset on restart. There is no actual AI inference, CRM read/write, email send, permission attestation, deployed public URL, persistent workflow state, or native custom builder.

No claim of five executable templates, 45 completed scenarios, or production-ready integration is made.

## Local operation

From repository root: `python app/local_server.py`; then open `http://127.0.0.1:8765` on the same host. Select the executable Lead Inquiry card, configure synthetic settings, validate the setup, and run an inquiry. The inspector shows redacted execution stages and review status.

## Follow-on product priority

1. Make the two-stage guided setup more resilient to browser refresh and stale results while retaining backend validation.
2. Add executable mock workflows #2–#5 only after each has a concrete input, observable outcome, refusal/duplicate handling and automated regression tests.
3. Broaden catalog metadata only with explicit `PREVIEW_NOT_EXECUTABLE` labels, without implying unbuilt functionality.
