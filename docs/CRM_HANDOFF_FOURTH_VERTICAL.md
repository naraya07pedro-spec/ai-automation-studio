# Fourth Executable Mock Vertical: CRM Handoff · Slice 32

Date: 2026-11-04. Development-only, loopback-bound, **no CRM integration or record creation**.

## Delivered

`CRM Handoff` is the fourth `EXECUTABLE_MOCK` template. The homepage offers a real interactive form that submits to `POST /api/mock-crm-handoff`. The Python `MockCRMHandoff` engine validates exact synthetic enumerations, applies a selected fixed mapping profile, checks a bounded process-local duplicate register, and returns a redacted mapped record **preview**, not a CRM write. The UI shows mapped fields, a six-stage server-derived trace, simulated review status, and the explicit zero-record outcome.

Catalog truth: **4 executable mock templates and 1 non-executable Knowledge & Document Routing preview**. There are not 45 implemented templates.

## Review and data boundaries

The input `review_acknowledged` is **caller-controlled**; it represents a local demo gate, NOT an authenticated or authorized human approval. A false value produces `REVIEW_REQUIRED_NO_RECORD`, with no mapping and no dedup registration. A true value yields only `CALLER_CLAIMED_MOCK_ACK`, not proof of authorization. Neither state can dispatch external work.

Allowlisted synthetic fields: `event_id`, `lead_stage` (new / qualified / needs_review), `service_code` (automation / integration / consulting), `owner_queue` (sales / solutions / operations), `mapping_profile` (standard / compact), and boolean `review_acknowledged`. Unexpected keys including names, emails, tokens, secrets and arbitrary free text are blocked.

`standard` mapping previews `crm_lifecycle_stage`, `crm_service_category`, `crm_owner_queue`. `compact` previews `stage`, `service`, `queue`. Values are enums only; no personal data or external customer identifiers are returned. The mock register is limited to 1000 unique IDs per process. Identical replays skip mapping, changed data with a reused ID fails as a conflict.

All responses explicitly report `crm_record_created: false`, `external_dispatch_count: 0`, and a final timeline step `CRM_WRITE: DISABLED`.

## How to run

From repository root run `python app/local_server.py`, then open `http://127.0.0.1:8765`. Select the CRM Handoff card, try an unacknowledged review (no mapped record), then mock-acknowledge and rerun the same synthetic ID. Repeat to see duplicate behavior. Change a mapped field while reusing the ID to observe a conflict.

## Limits and next slice

No actual CRM credentials, API calls, provider SDK, verified operator identity, durable event store, authenticated user accounts, production safety guarantees, or public deployment. UI and HTTP tests are not a full browser visual/accessibility test.

Next product slice: implement the fifth executable mock workflow `Knowledge & Document Routing`, preserve all prior workflow regressions, then prioritize actual browser QA, source code onboarding, a safe demo hosting architecture, and real integration gating if justified.
