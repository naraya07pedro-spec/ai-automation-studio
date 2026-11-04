# AI Automation Studio

A **local-only** automation studio prototype with one **functional SQLite-backed local rule workflow core** plus five deterministic synthetic mock workflow demonstrations. It is **not** a hosted SaaS, live AI agent, production integration platform or public internet server.

## Run the demo

Requires Python 3.11+ with SQLite available. No external provider credentials or API billing. Starting the server creates a local database at `~/.ai-automation-studio/local-workflows.sqlite3` for rule workflows and pseudonymous event history; it does not persist the five mock example runs.

```bash
python app/local_server.py
```

On the same computer, open **http://127.0.0.1:8765**. The server intentionally binds to `127.0.0.1` only. Do not expose it using public proxies or port forwarding.

## Functional local workflow workbench (new)

Scroll to **Build and run your first local rule workflow**. Create a private rule-based workflow with an allowlisted service filter, budget threshold and review/draft-only policy, activate immutable version 1, manually supply a pseudonymous event, and inspect the locally persisted outcome. Duplicate event IDs replay the original result; changing their payload causes a conflict instead of a second execution. Rules and audit history survive restart of the local server.

**Do not supply personal customer records, emails, phone numbers, secrets or real identities.** The server is unauthenticated, SQLite is not encrypted by the app, no connector is present, and everything runs on localhost. No outbound message or external CRM change occurs. The rule engine is deterministic, not generative AI. See [core specification](docs/LOCAL_WORKFLOW_CORE_V1.md) for exact endpoints, policy semantics, persistence and known gaps.

## Safe local rule versions and pause/resume (Slice 39)

Inside **Change rules without rewriting earlier results**, select an active or paused workflow, stage a new set of service/budget/review rules as a **draft version**, inspect the revision history, then explicitly **Publish**. Earlier event records retain the exact version and result they were processed under. A repeated event ID still replays the original outcome, even after rules change.

Use **Pause local workflow** to stop new local rule evaluations and CSV imports; **Resume** to opt back in. Publishing rules while paused **does not** automatically resume them. The controls require an expected version and change epoch; stale commands fail closed. Any CSV preview made before pause/resume/publish becomes invalid.

Older SQLite databases are migrated non-destructively on startup; stored local history remains intact. The core remains **localhost-only and unauthenticated**. Pause is **not cancellation of already committed work** and does not control any external system because no live provider adapters exist. [Full version-control and migration contract](docs/LOCAL_WORKFLOW_CONTROL_V1.md).

## Local CSV event intake (Slice 38)

Use the **Import events from a local CSV** panel below the functional local workflow workbench to import up to **25 small categorical events** at once, with an explicit no-write **Preview** before **Commit**. Both stages validate the exact file contents against the active workflow and its SQLite event ledger; stale previews, conflicting event IDs and invalid columns are rejected. Records share the existing durable local run history, and manual/batch duplicates use the same event fingerprint and decision rules.

Supported file layout (invented IDs only):

```csv
event_id,service_code,budget_tier,timeline
sample_001,automation,3,soon
sample_002,integration,1,flexible
```

Maximum CSV body size is 4 KiB. Only the explicitly allowed service, budget tier and timeline categories are accepted. **Do not import customer data, contacts, identities, messages or secrets.** Raw CSV contents are processed in memory and never stored in the SQLite ledger. This is *local manual import*, not an external integration, unattended trigger or AI automation.

See [CSV intake specification](docs/LOCAL_CSV_BATCH_INTAKE_V1.md) for data-flow, rejection cases, preview tokens, and remaining boundaries.

## Five executable *mock* workflows

| Workflow | Browser interaction | Actual side effects |
| --- | --- | --- |
| Lead Inquiry Qualification | Validate guided mock setup, run synthetic inquiry, inspect trace and review | None |
| Support Request Triage | Route a synthetic support ticket to a mock queue | None |
| Appointment Follow-up | Compare two explicit synthetic timestamps and prepare a reminder plan | None |
| CRM Handoff | Build an allowlisted mock mapped record preview | None |
| Knowledge & Document Routing | Evaluate synthetic document metadata and policy to preview a route | None |

The five mock workflows include input validation, bounded in-memory duplicate/conflict handling, and redacted outcomes. The separate local-rule core adds SQLite persistence for strictly categorical entries. There are no provider sends, real CRM writes, file transfers, AI model calls, stored customer data or persistent user sessions. Claiming local mock review does not authenticate an operator.

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

- `web/index.html`: responsive browser client for five mock demos and the separate functional rule workbench.
- `app/local_workflow_core.py`: standard-library SQLite rule lifecycle, immutable v1 activation and durable event history.
- `app/local_csv_batch.py`: bounded in-memory CSV parsing, no-write preview and transactional local event import.
- `app/local_server.py`: stdlib HTTP server; loopback only and intentionally unauthenticated.
- `prototype/`: deterministic isolated mock workflow engines, state/recovery reference implementation and runtime evidence checks.
- `tests/` and `tests_browser/`: backend HTTP/runtime and real-browser acceptance.
- `docs/PROGRESS.md`: chronological engineering changes and CI evidence.

There is **no** verified public deployment, comprehensive accessibility audit, durable external-action execution, custom visual graph builder, 45-scenario catalog, secured production server or real provider adapter in this prototype. The **local rule workbench** does have durable SQLite run history, while the five mocks remain process-local. The source architecture and CI evidence should be reviewed before relying on anything beyond synthetic sandbox experimentation.
