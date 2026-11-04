# AI Automation Studio

A **localhost-only** automation studio prototype with a functional SQLite-backed rule engine, manual CSV import, and **one optional public GitHub Issues read-only source adapter**, plus five isolated synthetic mock demonstrations. It is **not** a hosted SaaS, live AI agent, production-ready integration platform or publicly exposed server.

## Run the demo

Requires Python 3.11+ with SQLite available. No external provider credentials or API billing. Starting the server creates a local database at `~/.ai-automation-studio/local-workflows.sqlite3` for rule workflows and pseudonymous event history; it does not persist the five mock example runs.

**Slice 43: one-time local operator setup is mandatory before the normal server will bind.** Run in a terminal on your own computer:

```bash
python app/local_server.py --init-owner
python app/local_server.py
```

The first command prompts securely for a **12–128-character password** and confirmation, stores only a salted scrypt verifier at `~/.ai-automation-studio/operator.json`, and refuses to overwrite an existing owner. **Do not delete the owner file casually: there is no password recovery mechanism yet.** The second command loads that identity and fails closed when it is absent or invalid. Open **http://127.0.0.1:8765**, enter the password on the local login screen, and use **Lock session** to revoke the session.

The server binds only to `127.0.0.1`. Sessions are one-hour, short-lived process-memory tokens in HttpOnly, SameSite=Strict cookies; every mutation requires the matching session CSRF token, including old mock APIs and GitHub source preview. No real user accounts, cloud provider credentials, outside identity provider or GitHub login are created. Do not expose this unauthenticated-at-the-network-layer local HTTP service using public proxies or port forwarding. It remains unsuitable for real customer data or a compromised local PC.

## Optional one-time offline owner recovery (Slice 45)

A forgotten local owner password cannot be reset through the web UI. If you still know the current password, **stop the server** and generate a high-entropy, one-time recovery code in the local terminal:

```bash
python app/local_server.py --enable-recovery
```

Enter the current password when prompted. The CLI displays one 43-character random recovery code **exactly once**. Save it securely **outside this computer**; the owner file contains only a salted digest. The optional enrollment upgrades the existing version-1 owner verifier to a backward-compatible version-2 owner file, without touching workflow SQLite.

If you later lose the password but still possess the saved recovery code, **stop the local server** and run:

```bash
python app/local_server.py --recover-owner
```

Enter the one-time code and choose a new password. The code is **consumed permanently**, all sessions are revoked, and the existing SQLite history and approved public GitHub repositories remain unchanged. Restart the server and sign in with the new password, then enroll a *new* offline recovery code.

Both commands refuse to operate if a normal server is using `127.0.0.1:8765`. Normal authenticated password rotation also invalidates an enrolled recovery code. **There is no anonymous HTTP reset or guaranteed recovery if both password and code are lost.** Do not paste the recovery code into chat, tickets, GitHub Issues or screenshots. See [full offline recovery contract](docs/OFFLINE_OWNER_RECOVERY_V1.md).

## Password rotation and explicit public-source permissions (Slice 44)

The local owner can now use **Change operator password** to rotate the credential after proving knowledge of the current password. On successful atomic verifier replacement, **all operator sessions are revoked immediately**; sign in again with the new password. A storage failure leaves the previous credential and sessions unchanged. **Lost-password recovery is not implemented**; don't remove the owner file or assume there is an anonymous reset route.

Public GitHub read-only ingestion has an additional, persistent **default-deny exact-repository allowlist**. An authenticated owner must explicitly approve each `owner/repo` in **Allow a specific public GitHub repository**, re-entering the current local password. Approval is limited to at most 16 public repos; removing a grant prevents further fetches and invalidates all uncommitted snapshots for that source, even after a later regrant. A browser login does *not* by itself permit reading arbitrary GitHub repos.

The local source allowlist lives at `~/.ai-automation-studio/public-github-sources.json`; starting with no file means no approved external source. Source grants authorize only the existing **public, unauthenticated, fixed-host READ-only** GitHub Issues endpoint, not any GitHub API mutations, private repo, credentials or external dispatch. The synthetic issue #47 must also be explicitly approved on a real local server; only browser CI uses a disposable preapproved sample repo.

See [rotation and source permission contract](docs/LOCAL_OPERATOR_ROTATION_AND_SOURCE_PERMISSIONS_V1.md). The local server is still unsuitable for PII or public deployment.

## Local operator access gate (Slice 43)

Every ordinary app API, local workflow record, revision history, mock action, and public-source preview/commit now requires the provisioned owner's active session; POSTs additionally require a matching per-session CSRF header. Wrong passwords are rate-limited, cookies are HttpOnly, logout revokes sessions, and restarting the server invalidates all in-memory sessions. The HTML login shell and a minimal health endpoint remain readable so the operator can unlock the workbench.

This is **single-owner authorization on localhost**, not secure multi-user IAM. The local verifier file and SQLite remain on the same device and are not encrypted by the app; malware or other processes with the same OS user's privileges may bypass these controls. The loopback connection uses plain HTTP and cookies cannot be marked Secure. Existing backend unit tests inject intentionally unguarded test handlers to preserve historical pure-workflow evidence, while browser CI uses a separately defined **authenticated fixture server** with a test-only ephemeral owner password; the real `python app/local_server.py` never falls back to this fixture.

[Read the local operator security contract and limits](docs/LOCAL_OPERATOR_ACCESS_V1.md).

## Functional local workflow workbench (new)

Scroll to **Build and run your first local rule workflow**. Create a private rule-based workflow with an allowlisted service filter, budget threshold and review/draft-only policy, activate immutable version 1, manually supply a pseudonymous event, and inspect the locally persisted outcome. Duplicate event IDs replay the original result; changing their payload causes a conflict instead of a second execution. Rules and audit history survive restart of the local server.

**Do not supply personal customer records, emails, phone numbers, secrets or real identities.** The server requires one local operator session and SQLite is not encrypted by the app. Local rules and history stay on localhost; only the optional read-only GitHub source makes a user-initiated outbound HTTPS GET. There are no external writes, sends or CRM changes. The rule engine is deterministic, not generative AI. See [core specification](docs/LOCAL_WORKFLOW_CORE_V1.md) for exact endpoints, policy semantics, persistence and known gaps.

## Complete public GitHub issue → local SQLite acceptance (Slice 42)

A reproducible, **owned synthetic public issue fixture** exists at [issue #47](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/47). It is open, has the three explicit `aas:*` category labels, and contains no real customer data. One-time issue fixture setup was performed through the project owner's GitHub connection; **neither the product connector nor its acceptance workflow creates or edits GitHub issues**.

Run the exact numbered issue through the shipped public adapter and verify the original preview outcome is recorded with the same event ID, decision, reason and pinned rule version in a **temporary in-memory SQLite database**:

```bash
python scripts/probe_public_github_live.py \
  --repo naraya07pedro-spec/ai-automation-studio \
  --expect-issue-number 47 \
  --require-eligible \
  --check-ephemeral-import
```

The separate **Real labeled public issue to ephemeral SQLite** GitHub Actions job runs this exact contract as a **strict live-network gate**. Other labeled issues cannot substitute for the expected fixture. The probe fails if #47 is closed, loses labels, falls off page one, or GitHub is unavailable. It is read-only toward GitHub and has no persistent SQLite writes. The ordinary local browser workbench can also preview the labeled source when the user selects an active workflow, but its manual explicit commit **does persist** the chosen categorical event result on the user's own machine.

**CI proof must be inspected before calling this full E2E verified.** See [exact issue fixture and E2E audit contract](docs/PUBLIC_GITHUB_LABELED_E2E_V1.md) and [chronological CI evidence](docs/PROGRESS.md). These checks are not proof of authenticated private integrations, customer data security, AI execution, or external business-action writes.

## Real provider verification, not just mock CI (Slice 41)

The separate `Public GitHub live read-only verification` GitHub Actions job runs the **shipped public Issues adapter against GitHub's real REST API**. Its default safe probe reads `octocat/Hello-World` without credentials and reports a minimal, redacted JSON proof. This is distinct from the existing fully isolated Python tests and Chromium UI tests.

To reproduce the real-network check locally (internet required):

```bash
python scripts/probe_public_github_live.py --repo octocat/Hello-World
```

The statuses are intentionally precise: **verified transport** means an actual GitHub public GET succeeded; **verified live labels** requires a real open issue bearing all three `aas:*` labels; **verified ephemeral import** additionally requires an explicitly requested, temporary in-memory SQLite commit. Do not infer the latter two from the first. Only with an appropriate labeled *public* repo:

```bash
python scripts/probe_public_github_live.py --repo OWNER/REPO --require-eligible
python scripts/probe_public_github_live.py --repo OWNER/REPO --check-ephemeral-import
```

These commands do not create issues, use GitHub credentials, write to GitHub or modify the persistent local database. **Exact verification status and CI run evidence belong in [Progress](docs/PROGRESS.md)**. See [live verification instructions](docs/LIVE_PUBLIC_GITHUB_VERIFICATION.md).

## Read-only public GitHub Issues integration (Slice 40)

The **Inspect real GitHub issues, then choose whether to import** panel accepts a public GitHub repository as `owner/repo` (not a URL). With an active local workflow selected, click **Read public issues & preview locally** to request one page of up to 15 open issue/PR items from the official `api.github.com` REST API. Pull requests, issues with missing category labels and ambiguous mappings are ignored, and the UI shows only numeric-derived pseudonymous event IDs, three allowed rule categories, and local qualification decisions.

Each eligible issue must already have **exactly one** from each label group: `aas:service:{automation|integration|consulting}`, `aas:budget:{1|2|3}`, and `aas:timeline:{urgent|soon|flexible}`. The adapter does **not** create or modify issues or labels. Most arbitrary public repositories won't have these labels, so a successful read may produce zero eligible entries.

Source titles, bodies, usernames, comments and other free text are not stored or mapped. The operator then explicitly chooses whether to **Import reviewed issues to local SQLite**. A server-memory-only snapshot expires after five minutes and is rejected after a workflow change, pause/resume, new local event or repeated use. No browser storage or database row retains the original provider payload. There are **no GitHub writes, tokens, automatic polls or external sends**.

**Verification status:** the code genuinely implements a fixed-host read-only GitHub request, but CI tests isolate the provider using injected fixtures, and a successful real-network provider import has **not** been independently verified yet. See the [full integration boundary and verification plan](docs/PUBLIC_GITHUB_READONLY_INTAKE_V1.md).

## Safe local rule versions and pause/resume (Slice 39)

Inside **Change rules without rewriting earlier results**, select an active or paused workflow, stage a new set of service/budget/review rules as a **draft version**, inspect the revision history, then explicitly **Publish**. Earlier event records retain the exact version and result they were processed under. A repeated event ID still replays the original outcome, even after rules change.

Use **Pause local workflow** to stop new local rule evaluations and CSV imports; **Resume** to opt back in. Publishing rules while paused **does not** automatically resume them. The controls require an expected version and change epoch; stale commands fail closed. Any CSV preview made before pause/resume/publish becomes invalid.

Older SQLite databases are migrated non-destructively on startup; stored local history remains intact. The core remains **localhost-only and unauthenticated**. Pause is **not cancellation of already committed work** and does not control any external system because no external write-enabled provider adapters exist. [Full version-control and migration contract](docs/LOCAL_WORKFLOW_CONTROL_V1.md).

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
- `app/github_public_issue_intake.py`: opt-in HTTPS read from fixed official public GitHub Issues endpoint, strict label mapping and ephemeral snapshot approval for local-only import.
- `app/local_server.py`: stdlib HTTP server; loopback only and intentionally unauthenticated.
- `prototype/`: deterministic isolated mock workflow engines, state/recovery reference implementation and runtime evidence checks.
- `tests/` and `tests_browser/`: backend HTTP/runtime and real-browser acceptance.
- `docs/PROGRESS.md`: chronological engineering changes and CI evidence.

There is **no** verified public deployment, comprehensive accessibility audit, durable external-action execution, custom visual graph builder, 45-scenario catalog, secured production server or externally *live-verified* GitHub source run in this prototype. The **local rule workbench** does have durable SQLite run history, while the five mocks remain process-local. The source architecture and CI evidence should be reviewed before relying on anything beyond synthetic sandbox experimentation.
