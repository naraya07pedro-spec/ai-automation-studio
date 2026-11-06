# AI Automation Studio

## Private VPS deployment v2: secure credentials + live acceptance gate

**New after PR #99:** the existing loopback-only systemd VPS service can now opt in to encrypted managed webhooks, Gmail metadata OAuth and single n8n outbound webhook using private root-owned systemd `LoadCredential` files rather than embedding vault master keys or webhook secrets in unit files. A hidden-prompt credential provisioner and read-only `deploy/verify_private_vps_runtime.py` check systemd service state, exact private bind, secure Mission Control response and anonymous API rejection. The original Windows SSH tunnel and schedule persistence path stay unchanged. **No Tencent VM was accessed, started or deployed from CI; Google consent and live n8n delivery remain unverified.** [Production setup, secure credential requirements, and live acceptance runbook](docs/PRIVATE_VPS_CREDENTIAL_READINESS_V2.md).

## Native Google Gmail OAuth: metadata-only (explicit opt-in)

Mission Control **09 · Google Gmail metadata** now has a real direct official Google OAuth 2.0 Desktop application connector (PKCE S256, state-protected loopback callback) with a distinct AES-256-GCM encrypted refresh grant. Set up your own Gmail API project/Desktop OAuth client, opt in via `python tools/connect_google_gmail.py --client-id YOUR_GOOGLE_DESKTOP_CLIENT_ID`, then launch `START_AI_STUDIO.cmd --mode offline --enable-google-gmail-metadata`. Each official Gmail API read requires a fresh owner password and returns only account counts or five inbox headers. **No sending, changing messages, auto-scheduling, agent tool access, shared ChatGPT plugin credentials, or verified live Google consent is claimed.** The Gmail metadata OAuth scope is **restricted** and may require Google verification for public distribution. [Configuration, verification and privacy limits](docs/GOOGLE_GMAIL_OAUTH_METADATA_V1.md).

## Encrypted connector vault (AES-256-GCM, explicit opt-in)

**New:** managed HTTPS/n8n connections can now use a real authenticated AES-256-GCM encrypted registry, with the 256-bit master key supplied independently via `AAS_VAULT_MASTER_KEY`. It refuses wrong keys and changed ciphertext, never silently downgrades to plaintext, preserves existing per-connector no-retry delivery ledgers, and provides an offline verified migration tool for the old plaintext registry. Install `python -m pip install -r requirements-vault.txt`, generate a private key offline with `python tools/migrate_managed_webhook_vault.py --generate-key`, save it privately, and start explicitly with `START_AI_STUDIO.cmd --mode offline --enable-encrypted-managed-webhooks` after setting your key in the process environment. **Default legacy `--enable-managed-webhooks` still stores plaintext and must not be mistaken for the encrypted mode.** [Vault setup, migration, key backup and threat limits](docs/ENCRYPTED_CONNECTOR_VAULT_V1.md). Webhook vault alone does not grant Google OAuth, KMS, all-plugin access, verified n8n delivery or Tencent VPS activation.

## Multi-webhook Connector Manager: private manual connections

Mission Control section **08 · Managed connections** now manages **up to four independently registered n8n/custom HTTPS webhook destinations**, with a separate SQLite delivery ledger per connection. The owner can register/revoke a URL and optional signing secret without editing code, preview one already-saved immutable workflow event, confirm a single outbound POST using a fresh owner password, and inspect destination-specific receipts. Feature is disabled unless launched with `START_AI_STUDIO.cmd --mode offline --enable-managed-webhooks`. **Secrets are OS-permission protected but NOT encrypted at rest**; check Windows ACLs before storing any high-value URL/secret. No AI-model write permission or scheduled dispatch for these managed connections; the older single-destination autopilot is independent. [Setup and security limits](docs/MANAGED_CONNECTORS_REAL_V1.md).

## Owner-armed recurring HTTPS/n8n autopilot (bounded external writes)

Mission Control section **07 · Scheduled webhook autopilot** now joins the durable minute-to-month local scheduler to the existing real HTTPS/n8n outbound connector. The owner must separately preview an exact ACTIVE schedule, one configured destination, source rule version, a maximum of **1–30 total POST attempts** and a **1–90-day expiration**, re-enter the owner password and explicitly approve. After that, the worker can send real fixed-schema pseudonymous local rule receipts at upcoming scheduled due times without new browser clicks. Every claim is saved to SQLite before network traffic; any uncertain or failed external result quarantines the policy with **no blind retries**. This is disabled until the owner configures the webhook, enables it at server startup, and arms a schedule. **No arbitrary AI plan scheduling, all-plugins marketplace, CRM/email writes or verified Tencent deployment.** [Activation and safety documentation](docs/SCHEDULED_WEBHOOK_AUTOPILOT_V1.md).

## Connector Hub: one real, owner-approved HTTPS external write

**New:** Mission Control section **06 · Connector Hub** can send one previously saved, pseudonymous local workflow decision to an **owner-configured n8n / custom public HTTPS webhook**. The exact payload is reviewed, a single-use 5-minute preview token is issued, the current owner password is revalidated and a separate click authorizes one POST. All attempts have a persistent ledger and a stable delivery ID; ambiguous outcomes **never retry automatically**. The URL and signing secret are configured as **private server environment variables**, not in the browser. External sending remains **disabled by default**. Launch explicitly with `START_AI_STUDIO.cmd --mode offline --enable-webhook` after setting `AAS_WEBHOOK_URL`. A response **2xx is endpoint acceptance, not downstream success**. [Setup, n8n mapping, and limitations](docs/OUTBOUND_CONNECTOR_REAL_V1.md). This is **NOT** an all-plugins marketplace, generic model-selected external action, or verified live Tencent deployment.

## Real recurring schedules: 1 minute through monthly

**New:** Mission Control now includes a **server-executed scheduler** for ACTUAL persisted local deterministic rule workflows: every N minutes/hours, daily, weekly or monthly in WIB/UTC. The application worker starts with the Python server, saves schedule definitions and run receipts in SQLite, persists through restart, handles one safe catch-up after downtime, and supports pause/resume and owner-visible history. To use it, first create + activate a local workflow in section 04, then set a recurring trigger in section 05. **No Ollama model is required for deterministic scheduled evaluations.** The browser can close; the Python process must stay online. **No external writes, unrestricted agents, universal plugin connections, or public VPS hosting are implied.** [Start guide and recovery behavior](docs/SCHEDULER_REAL_V1.md).

## Mission Control: use the functional app first

Launch the app with `START_AI_STUDIO.cmd` (Windows) or connect to an already running private VPS via `CONNECT_PRIVATE_VPS.cmd`; both now open **http://127.0.0.1:8765/mission** after confirming the loopback HTTP service is responding. This is the **real task-first interface**: sign in, detect a local Ollama model, ask a question, propose a durable 1–3-step plan, inspect/approve exact tool arguments, execute approved steps, monitor server-owned read-only runs and download a sourced report. **Even without Ollama**, use the same page to create/activate/run a real SQLite-backed deterministic workflow with pseudonymous events. All of these use existing authenticated server APIs; there are no mocked responses on this new page. The old five synthetic demo panels remain in the [Advanced workbench](http://127.0.0.1:8765/).

**Boundaries:** no public URL or confirmed Tencent deployment, no unsolicited model testing, no general shell/browser autonomy, no external email/CRM writes, a rule-workflow scheduler but no unrestricted AI-agent scheduling or inferred permission grants. Installed Ollama and its model are required for AI inference. Never enter sensitive customer data in the local workflow test. [VPS runbook](docs/PRIVATE_VPS_ALWAYS_ON_V1.md). Device-specific acceptance remains deferred as previously requested.

## Always-on PRIVATE Linux VPS deployment (SSH tunnel, NOT public HTTP)

This repo now includes a reviewed **Linux systemd service installer** (`deploy/private_vps_service.py`) and **Windows SSH tunnel launcher** (`CONNECT_PRIVATE_VPS.cmd`). The app can stay running on a **verified, active VPS** when the operator's Windows PC is off; to use it remotely, connect via encrypted SSH and open the existing `http://127.0.0.1:8765`. Its server is **still localhost-only** and must **not** be exposed through a public firewall port or reverse proxy. A VPS-side Ollama model is separately required for agent inference; the service running 24/7 is **not** unattended task scheduling or business-action automation. **No live Tencent VPS deploy or public URL has been verified.** [VPS setup, controls and live acceptance gate](docs/PRIVATE_VPS_ALWAYS_ON_V1.md).

## Run the actual local app now (Windows)

**Double-click `START_AI_STUDIO.cmd`** from a complete checkout with Python 3.11+. On first launch, create a private local owner password; the app opens at **http://127.0.0.1:8765**. Existing workflows and optional permissioned local-agent tools can then be used in the browser. No cloud account or Administrator permission is required to **start the local app**. For deterministic workflows without Ollama: `START_AI_STUDIO.cmd --mode offline`. An installed, independently running Ollama model is required for AI inference and has not yet been tested on the owner's PC. **This is not 24/7 public hosting** and does not yet send emails or write to live CRMs. See [Launch instructions, scope and deployment gate](docs/LAUNCH_NOW_V1.md).

AI Automation Studio is a **local-first, open-source AI agent and automation workbench**, not a hosted SaaS. Its current capabilities include an opt-in **real local Ollama model inference and bounded read-only tool loop**, limited persistent conversations, **explicitly approved durable read-only task plans**, an optional permissioned public GitHub issue reader, a SQLite-backed deterministic workflow core, manual CSV ingestion, one permissioned public GitHub Issues read-only adapter, and five explicitly synthetic mock workflows. It is **not yet** a general autonomous agent, trusted production integration platform or publicly exposed server.


## Independently check one published Greenhouse ATS job (Slice 80)

A separately opted-in, authenticated **owner-clicked** official [Greenhouse Job Board API](https://docs.greenhouse.io/job-board) check now verifies whether an **exact job post** is publicly retrievable on the provider's board at check time. Enter the employer's public Greenhouse board token and exact numeric job post ID, then click **Check official ATS posting (read-only)**. View a sanitized bounded title, location label, company name, description excerpt, timestamps, canonical Greenhouse link and source fingerprint. One fixed TLS-verified `GET` goes to `boards-api.greenhouse.io/v1/boards/<token>/jobs/<id>`; no arbitrary host, redirect, application POST, CV submission or model tool invocation.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-greenhouse-evidence
```

An optional HN story ID is only an **owner-supplied, unverified reference**, not an independently established HN-to-ATS match. Even a publicly accessible ATS posting **does not prove** it accepts applications now, supports remote work, permits a candidate in Indonesia or is suitable for the candidate's tenure. These fields are explicitly marked unverified. The registry keeps this public read out of both model-generated plans and background/server batches. The read is response-only, with only payload-free audit events stored. [Detailed ATS evidence contract](docs/GREENHOUSE_PUBLIC_ATS_EVIDENCE_V1.md). Live CI checks one real currently listed Greenhouse company job. Real owner-PC Ollama/Windows acceptance is still deferred.

## Discover recent HN public job titles before per-ID permission (Slice 79)

The authenticated local workbench now has a separate **user-clicked discovery preview** for the [official Hacker News `jobstories` API](https://github.com/HackerNews/API). It lists up to **five** latest public HN job titles, IDs, canonical source links and timestamps, using **at most six read-only HTTPS requests** to fixed official endpoints. Titles are sanitized untrusted public metadata, not verified employment opportunities or eligibility decisions.

Click **Preview latest 5 HN job titles**, inspect a source link and select an ID. **Copy selected ID into grant form** only fills the ID field: it does *not* grant permission, create a plan, or execute an agent. Explicitly re-enter the local password to grant that exact ID, then separately review, approve and execute an `hn_public_story` task to obtain its content.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-hn-story-read --enable-hn-job-discovery
```

The new `hn_jobstories_preview` registry entry is `PUBLIC_READ`, owner-click-only, **not** model-plannable or batch-enabled. Preview results are not persisted to plan SQLite and never grant IDs automatically; audit logs record events without titles/IDs. No ATS verification, employer authenticity, Indonesia work eligibility, full HN scraping, application, external mutation or provider credential is implied. [Exact limits and verification rules](docs/HN_JOB_DISCOVERY_V1.md). Real local Ollama/Windows machine validation remains deferred.

## Independent external read-only provider: exact owner-granted Hacker News story (Slice 78)

The local planner now supports **a second external provider beyond the existing public GitHub issue reader**. Use an explicitly owner-granted Hacker News **public story/job item ID**, never an arbitrary URL, to read the public item and at most **two direct comments** from the official Firebase-backed HN API, with strict HTML-to-text sanitization and a durable SHA-256-backed provenance receipt. The provider data is untrusted public user prose; HN job posts are **not** confirmed to be live, legitimate, Indonesia-eligible or open for applications.

Start the loopback workbench with:
```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-hn-story-read
```

After logging in, grant an exact numeric story ID in the HN owner-permission panel using your **current local password**. The grant is default-deny, persistent and versioned; revoking or changing a grant invalidates earlier plan approvals. Review the model-proposed `hn_public_story` step, approve the task and separately execute it. The model sees **only granted numeric IDs during planning**, not remote item text. No private HN account credentials, custom URLs, external writes, scraping of whole threads, automatic polling or job applications are added.

[Detailed HN read-only provider/grant contract](docs/HN_PUBLIC_STORY_CONNECTOR_V1.md). Tests include injected synthetic provider responses and a separately verified **real public HTTPS GET** in CI when the official API is reachable. Local Windows/Ollama machine acceptance remains deferred.

## Frozen tool registry and separately approved local JSON actions (Slice 77)

A code-owned [tool-effect registry](docs/LOCAL_TOOL_ACTION_GATE_V1.md) classifies the eight existing reviewed read tools and both owner-only local-create actions: the **existing** Markdown report export and **new** `local_csv_profile_json_export`. Neither write action can be model-planned or run in the existing server-owned read-only batch; both server endpoints now enforce their registry permissions.

After a previously reviewed and completed CSV profiling plan, click **Preview CSV profile JSON (no write)** to inspect the exact **aggregate-only** JSON and SHA-256, then separately **Confirm & save verified JSON** with a single-use, 5-minute server-issued preview ticket. The server recomputes the entire original CSV profile against the frozen source before writing, logs the write attempt and outcome to its local security ledger, and uses a fixed create-only file destination:
`~/.ai-automation-studio/exports/local-agent-csv-profile-<plan-id>.json`. It never overwrites changed files. Identical repeated exports are safe/idempotent.

Enable it only with all flags:
```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-plan-csv --enable-plan-artifacts --enable-plan-actions
```

The file is **plaintext** and may disclose aggregates; do not use confidential datasets, credentials or PII. No generic filesystem, shell, email, webhook, external API write, model-selected path or scheduled action is granted. Source-data validity remains the owner's responsibility. Real owner-PC Windows/Ollama acceptance remains deferred.

## Verified CSV-to-calculator numeric handoffs (Slice 76)

An owner-approved task plan can now use numeric aggregates from a **prior, explicitly selected and executed CSV profiling step** as typed calculator operands. Example reviewed plan: profile `revenue`, then multiply `{"from_step":1,"field":"column_sum","column":"revenue"}` by `2`. Strict supported fields are `row_count`, `column_sum`, `column_nonempty_count`, `column_missing_count` and `column_distinct_count`, all bound to an earlier approved `local_csv_profile` step; column metrics require a column name previously selected in that step.

The executor checks the original frozen CSV digest and **recomputes the full saved deterministic profile** before accepting any numeric reference. Tampered output, nonexistent fields/columns, disabled CSV permission, nonnumeric sums, zero divisors and out-of-bounds operands are blocked **before the calculator dispatch**, with a persisted audit including source-output SHA-256 and CSV SHA-256. The existing approval, one-step/manual execution or separately confirmed bounded server-run semantics remain unchanged. No model-selected path, CSV row echo, remote write, autonomous planning or new source permission is introduced. [Contract and limits](docs/CSV_TYPED_HANDOFFS_V1.md).

## Owner-selected CSV profiling inside reviewed task plans (Slice 75)

The authenticated local workbench can now perform **deterministic column profiling on one explicitly owner-selected, short UTF-8 CSV**, as a persistent reviewed plan step. Opt in with:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-plan-csv
```

Select a `.csv` file in **Plan a small local task**, explicitly consent to saving its local plaintext snapshot, and propose a goal. The local model sees **only** filename/byte count/SHA-256/row count/column names, never the records. A fixed `local_csv_profile` step can select 1–8 exact columns and, after separate owner approval/execution, computes nonempty/missing/distinct counts and bounded Decimal min/max/sum/mean when values are numeric. The reviewed profile is persisted for the existing final-report workflow and opt-in export. No shell, arbitrary filesystem access, external writes, model-generated data claims, CSV row echo, or automatic execution is added.

Limits: up to **6,000 UTF-8 bytes, 100 data rows, 8 columns**; strict headers/rows; selected CSV is **plaintext in SQLite** and unsuitable for secrets, confidential records or customer PII. Browser/file snapshots cannot be silently changed after approval. This feature is not a spreadsheet editor or multi-dataset analysis. Real Windows/Ollama acceptance remains deferred. [Complete contract](docs/OWNER_SELECTED_CSV_PROFILE_V1.md).

## Owner-approved local task deliverable files (Slice 74)

After an explicitly approved read-only plan has completed and the owner has separately generated its evidence-linked report, the workbench can save that **existing report** to an actual local Markdown file without re-invoking a model. Start with `--enable-local-agent --enable-agent-plans --enable-plan-artifacts`, then use **Save .md into local workspace** and accept the exact write destination in the new native confirmation.

The only writable destination is the server's fixed `~/.ai-automation-studio/exports/local-agent-report-<plan-id>.md` workspace. The browser supplies only the saved plan ID; the server validates the report's quoted evidence, escapes untrusted Markdown, writes once without overwriting existing files, and returns an SHA-256 receipt. Identical export repeats are read-only/idempotent. The security audit records export attempt/success without report contents. There is no arbitrary file writing, automatic export, model-directed file path, cloud upload, email, or OS service. **Files are plaintext, and model interpretations are unverified**. Windows/Ollama owner-device acceptance remains deferred. [Full workflow and security contract](docs/LOCAL_PLAN_ARTIFACT_EXPORT_V1.md).

## Server-owned execution of reviewed plans (Slice 73)

After reviewing and explicitly approving a bounded, read-only 1–3-step task plan, the owner can click **Run reviewed steps on local server** and confirm the exact remaining tool names and arguments. One server worker executes steps sequentially using the existing model gate, records each result in the SQLite ledger, and continues while the **local server remains alive even if the browser closes**. Reload the plan ID for live saved status or use **Request server stop after current step** to prevent later steps.

A service crash **never automatically replays work**; any uncertain in-flight action is marked for inspection. The tool allowlist stays read-only; this is **not** a persistent OS service, unattended scheduling, external write connector, or an agent that survives server shutdown. Windows/Ollama live acceptance remains deferred. [Full contract and limitations](docs/SERVER_OWNED_READONLY_BATCH_V1.md).

## Typed numeric handoffs across approved task steps (Slice 71)

Reviewed task plans can **reuse a saved numeric result in a later calculator step** without the owner copying values by hand. For example, the local agent may propose `7 × 8` followed by `previous output × 2`, and each step still requires a separate owner click. A stricter example reads visible-label counts from **previously permissioned** public GitHub issue triage and calculates a flagged-share percentage, without additional GitHub requests or model-generated data interpretation between the steps.

The only supported reference is a two-key object such as `{"from_step":1,"field":"output"}`. It must point strictly backward to a preceding `calculate.output`, or to one of `github_issue_triage.returned_count`, `review_flagged_count`, `unassessed_count`. No arbitrary text fields, source documents, paths, JSONPath, code, or general AI-generated answers can flow into operands. The executor resolves only recorded outputs, rechecks calculator bounds and triage-count consistency **before** starting the next step, and refuses invalid sources with HTTP 409. Every successfully resolved value and SHA-256 source fingerprint is saved as an audit log visible in the plan and final Markdown report, with legacy plans migrated unchanged.

This is opt-in through the existing `--enable-local-agent --enable-agent-plans` flags; using public GitHub counts also requires `--enable-agent-github-read` and an explicit source grant. **No auto-execution, new external-write tool or real PC Ollama test is introduced.** Automated CI uses synthetic model fixtures; actual Windows/Ollama acceptance remains deferred. [Complete typed binding contract](docs/TYPED_STEP_HANDOFFS_V1.md).

## Multi-document comparisons inside owner-reviewed task plans (Slice 70)

A task plan can now use the existing local source-cited retrieval engine to compare **2–3 explicitly selected short text/Markdown files** in a **single approved plan step**. This is an additional real data source for durable tasks, not a temporary Q&A window or unrestricted file search.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-multi-documents
```

In **Plan a small local task**, choose 2–3 files using **Or select 2–3 short documents to compare**, then explicitly authorize storing the chosen snapshots in the local plaintext SQLite task-plan ledger. File bounds are **2,500 UTF-8 bytes each, 6,000 bytes total**. The local model receives only filenames, sizes and SHA-256 digests while proposing a **local_multi_document_question** step. Only after owner approval and a separate **Execute next step** click does the analyzer receive source contents, use bounded lexical retrieval, verify source-specific quotes and save the answer in the plan. A finished plan can produce the existing Task Deliverable.

One-file and multi-file selection are **mutually exclusive**; neither is granted by default. An old plan migrates without gaining document access, and restarting without the `--enable-multi-documents` flag fails closed. Plan list/details show metadata only until results have been explicitly generated, although those results can quote the source text. SQLite snapshots remain plaintext and are unsuitable for secrets or confidential records. GitHub CI uses synthetic model transport, **not** the user's Windows/Ollama hardware.

[Full workflow, limits, source permissions and trust boundaries](docs/REVIEWED_MULTI_DOCUMENT_PLANS_V1.md).

## Owner-selected document research inside approved task plans (Slice 69)

The local agent can now include **one explicitly selected text/Markdown document** in a durable, human-reviewed task plan, rather than handling every document only as an isolated one-shot Q&A. This is a substantive source expansion for core agent tasks.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-local-documents
```

Under **Plan a small local task**, choose a single UTF-8 `.txt` or `.md` file of at most **3,500 bytes**, then **explicitly consent to saving its text in the plan's local plaintext SQLite**. A plan may propose the new bounded, read-only `local_document_question` tool against **only this owner-selected snapshot**, never an arbitrary file path. During proposal the model sees file metadata and the goal, **not the document body**. The full text is used only when the owner reviews/approves the plan and presses **Execute next step** for that document question. The answer uses the existing exact-quote checks, and the finished plan can produce the previous Task Deliverable and Markdown export.

Source document bodies are **not returned by plan list/get APIs**, although the task-plan SQLite intentionally stores the chosen source until deletion. Plan responses show filename, byte count, SHA-256 digest and whether a snapshot is stored; restarts reuse that frozen version without opening PC files. No folders are scanned and no files are uploaded to cloud models. This feature does not support PDFs, secrets, private customer records or automatic file discovery. The owner has **deferred actual Ollama-on-Windows acceptance**; CI checks use labeled synthetic inference.

[See the complete consent, API, storage and security contract](docs/SELECTED_DOCUMENT_PLAN_WORKFLOW_V1.md).

## Completed-plan Task Deliverables (Slice 68)

AI Automation Studio now offers a **final, reusable deliverable** after an owner-approved read-only plan finishes. Previously, completed task plans exposed only raw tool results. This workflow synthesizes those **already recorded results** into a bounded, explicitly requested local AI report with checkable quotes. There is no automatic new tool call, crawl or external fetch.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans
```

In the authenticated local UI, finish a plan as usual, then click **Generate final report**. A single local Ollama call proposes a short summary and 1–3 findings. Every finding must cite an **exact substring from the corresponding saved step output**, checked by the server. Interpretations remain **UNVERIFIED**, even if those quotes match. The report persists in the existing SQLite plan record, can be reloaded without model inference, and is available with explicit **Copy report** and **Download .md** buttons. Older completed plans can also request a report without replaying steps. Invalid reports are rejected without being stored.

Only the first 3,500 characters from each completed step are supplied to the model. The app does **not** add external write authority or broaden permitted tools. SQLite report content remains local plaintext and should not contain secrets. GitHub CI tests browser and protocol with labeled synthetic models, not an actual Windows Ollama installation; the owner has explicitly deferred live device acceptance. [Full implementation, validation and trust boundaries](docs/COMPLETED_TASK_DELIVERABLES_V1.md).

## Memory-backed, owner-reviewed agent plans (Slice 67)

The local workbench now links **explicitly selected memory cards** to its persistent, human-reviewed read-only task plans. Enable both opt-in features:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-memory --enable-agent-plans
```

In the authenticated UI, write or inspect memory cards, then under **Plan a small local task**, enter a task goal and optionally up to **3 note IDs** in **Memory IDs for THIS proposed task plan**. The local model receives only those selected cards, labeled unverified, and proposes 1–3 fixed-tool steps. The plan saves the selected note IDs, revisions, and content fingerprints alongside its tool arguments, with **no automatic execution**. Review the complete plan, click **Approve**, then **Execute next step** for each read-only action. If a note is edited or deleted after proposal, a stale plan receives `memory_context_changed` before approval or further step execution; cancel it and propose a fresh plan. Previous plans without memory retain their behavior. No note grants shell, file scan, email, extra GitHub access or other side effects.

Memory and plan ledgers are **plaintext local SQLite**, not appropriate for secrets. The real Windows/Ollama device acceptance remains deferred by owner decision; automated CI checks cover code behavior and injected browser fixtures only. [Full contract, threat model and recovery](docs/MEMORY_BACKED_AGENT_PLANS_V1.md).

## Owner-curated local AI memory workspace (Slice 66)

The local agent now supports a **separate, optional memory workspace** that carries owner-typed notes across chat threads and restarts, without automatically copying all saved context into every model request. This is a real user-facing capability, not another synthetic agent demonstration.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-memory
```

After authenticating in the local browser, open **Saved memory cards** under **Ask your local AI agent**. Create a note with a readable lower-case ID, view/list it, load it to edit, or explicitly delete it. A note may contain up to **600 characters**; the local store holds up to **24 cards**. Updates and deletes use revision checks to prevent unintended stale overwrites. In a chat request, enter up to **3 saved note IDs** in the **Use saved note IDs** field. The agent receives **only those selected notes**, for that request. The response returns the exact IDs used, and the next request has no selected memory unless its own request also supplies IDs.

**Storage is plaintext local SQLite.** Do not save passwords, confidential records, credentials or sensitive customer data. Notes are marked `OWNER_CURATED_UNVERIFIED`, never automatically created by a model, and are not independently verified facts. Memory edits/deletes never call Ollama or contact cloud services. This does not grant file scanning, shell execution or external write permissions. [Full feature contract, auth and limitations](docs/OWNER_CURATED_MEMORY_V1.md).

**Build-first:** the owner has deferred live Ollama testing on their own Windows computer while core features are being built. GitHub CI can still check deterministic storage, permissions, API semantics and synthetic browser interactions; it cannot establish live hardware compatibility. [Deferred device acceptance](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).

## Optional local AI agent in the browser (Slices 47–50)

Install Ollama separately, download a **tool-calling-capable** local model that your computer can run, and ensure Ollama is available on its default local port. After provisioning the owner's password once (see below), start the workbench with:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-history
```

Visit `http://127.0.0.1:8765`, log in, open **Ask your local AI agent**, and enter the name of a locally installed model. The agent can use a **calculator** and **read-only UTC clock**, but not run shell commands, access arbitrary files, send messages, or mutate external systems. A request can run stateless or, when you explicitly check **Save this conversation on my PC**, store and reuse context in a selected conversation ID. Up to **8 saved conversations × 6 user/assistant pairs** are retained. Use the UI to view and delete a selected conversation; existing Slice 49 records are migrated into `default`.

**Important:** chat history is plaintext SQLite on your machine and is unsuitable for secrets or customer PII. Ollama hardware/model compatibility has **not** been verified through a real model E2E in CI. All server access remains localhost-only and requires local operator authentication. Without the two startup flags, local inference and persisted agent history are disabled by default. See [agent conversation and migration documentation](docs/LOCAL_AGENT_CONVERSATION_ISOLATION_V1.md) and [progress log](docs/PROGRESS.md).

## Check a real installed Ollama model (Slice 51)

Green synthetic CI tests do **not** prove a model works on your Windows hardware. Use the new explicit **real localhost readiness probe** before treating your installation as compatible:

```powershell
python app/local_ollama_readiness.py
python app/local_ollama_readiness.py --model qwen2.5:3b
```

The model argument above is an **example** and must be replaced with the name of an installed, tool-capable local model. Without an installed model the check reports a blocked status; it will not download one. In the authenticated browser, **Find installed Ollama models** lists local names; **Test local model & tool calling** requests one bounded calculator-tool round trip. `OBSERVED` proves that this tool call ran in the local engine during the probe, not the model's general accuracy. The probe never saves history or calls third-party business APIs. [Read the full scope and constraints](docs/REAL_OLLAMA_READINESS_V1.md).

## Opt-in AI task planning with human approval (Slice 52)

The browser now offers **Plan a small local task**, backed by real optional Ollama model inference and a durable SQLite plan ledger. The model can propose up to **3 explicitly bounded read-only steps** (calculator or UTC clock), but **nothing executes when the plan is proposed or approved**. The owner reviews exact arguments, approves the plan and manually triggers each step. State survives restart; interrupted operations never silently resume.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans
```

Task plans are **disabled by default**, separate from the optional conversation history. The plan DB stores goal text, arguments and results as **plaintext** and is not suitable for credentials or customer data. CI exercises the real state machine with model fixtures; it does not replace testing an installed model on your Windows PC. [Read the task plan contract and security limits](docs/LOCAL_AGENT_TASK_PLANS_V1.md).

## Operator-allowlisted GitHub issue reads in agent plans (Slice 53)

The optional durable plan runner can now fetch **real open issue metadata from a specific public GitHub repository** through the existing fixed-host, read-only connector. This is the first externally sourced plan step, not an uncontrolled browser or GitHub editing assistant.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-agent-github-read
```

After owner login, use **Public Source Permissions** to allow the exact public repository; then enter an installed local model, request a GitHub issue-list plan, review the `github_public_issues` step, approve it and click **Execute next step**. Creation and approval do not fetch the external source. Revoking a source grant invalidates old plan approvals, even after regrant. Only the first 15 public open issue entries are examined, with bounded title/label/number/URL metadata; issue bodies, private content and all writes remain disabled.

This feature is disabled by default and is not a substitute for testing Ollama with a real installed model on the user's PC. [Read tool permissions, output provenance and limitations](docs/AGENT_GITHUB_READ_TOOL_V1.md).

## Source-backed GitHub issue triage and Windows model acceptance (Slice 54)

The approved-plan system can now propose a `github_issue_triage` step. It performs **one explicitly approved public GitHub GET** on a granted repository and produces an auditable label-based report: issue category, exact evidence labels, review flags, unknown priority, issue links and sampling limits. It does **not** infer actual incident severity, inspect issue bodies, or edit GitHub.

Enable the optional feature and grant your chosen public repository under **Public Source Permissions**:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-agent-github-read
```

A separate local acceptance command checks a **real installed Ollama model**, requests an exactly constrained triage plan and waits for typed operator consent before fetching public issues:

```powershell
python app/local_agent_e2e_smoke.py --model qwen2.5:3b --repo Octocat/Hello-World
```

Replace the model and repository with an actually installed, compatible model and a **previously approved** public repo. Without typed `EXECUTE Octocat/Hello-World`, it does not fetch from GitHub. The model weights and Windows hardware compatibility have **not** been verified by GitHub CI. [See triage rules, privacy boundaries and genuine Windows acceptance](docs/AGENT_ISSUE_TRIAGE_AND_E2E_V1.md).

## Source-linked local Ollama issue analysis (Slice 55)

An operator can now authorize a `github_issue_analysis` task-plan step to fetch **real public GitHub issue metadata** from an explicitly granted repository and ask a **locally installed Ollama model** for up to five review questions. The model can select only issue numbers in that exact fetched source snapshot; title, labels and GitHub URLs are always taken from the source, not invented by AI. All AI observations are visibly **unverified interpretations**, never established severity, root cause or security findings.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-agent-github-read
```

After owner login and granting the exact repository under **Public Source Permissions**, enter a compatible installed model and request "Analyze open GitHub issues from Octocat/Hello-World and suggest review questions". Review the proposed tool/repo, approve, and separately execute the step. Proposal and approval **do not contact GitHub or invoke analysis**; execution performs one permitted GitHub GET and one bounded model inference. No external writes or private issue access. These observations are stored in **plaintext local SQLite** until the plan is deleted.

CI validates this using injected fake model/provider fixtures, not a real installed Ollama model on your Windows computer. [Read the source/citation verification and limits](docs/LOCAL_GITHUB_ISSUE_ANALYSIS_V1.md).

## Ask about an operator-selected local text file (Slice 56)

Beyond GitHub, AI Automation Studio now supports **explicitly selected local UTF-8 `.txt` and `.md` files** for evidence-linked local Ollama Q&A. This feature is separate from persistent agent plans and does **not** scan the PC or save document contents to the app's SQLite history.

```powershell
python app/local_server.py --enable-local-agent --enable-local-documents
```

After owner login at `http://127.0.0.1:8765`, enter your installed local model name, choose a text/Markdown file of up to **6 KB**, and ask a question. The model's answer must include **exact quotes from numbered source chunks** that the server validates as verbatim substrings. If the quote doesn't exist, the response is rejected. The model's *interpretation* is still unverified, even when the quote matches exactly. Only the selected file's text is sent to the local Ollama endpoint; no cloud model, filesystem scanning, or external writes are enabled.

This is not PDF support or full-document RAG. GitHub CI uses fake Ollama inference; **a real model and Windows hardware acceptance are still outstanding**. [Read the selected document scope and security/evidence contract](docs/LOCAL_DOCUMENT_ANALYSIS_V1.md).

## Selected multi-document research and real Windows model smoke (Slice 57)

The local-first workbench can now answer a question across **2–3 explicitly selected UTF-8 `.txt`/`.md` documents** without crawling folders, uploading to cloud AI or saving a document index. A deterministic word-overlap retrieval step chooses at most five short source passages for local Ollama. The model returns an answer and exact quotes, which the app verifies against the **correct original filename and character offsets**. The answer's interpretation remains unverified even when quotes match.

```powershell
python app/local_server.py --enable-local-agent --enable-multi-documents
```

After owner login, select 2–3 distinct small files (each <=2,500 bytes, combined <=6,000 bytes) in **Ask across 2–3 selected documents** and enter your installed Ollama model. This is separate from the original single-file `--enable-local-documents` switch.

For actual model acceptance on your own Windows device, run:

```powershell
python app/local_ollama_readiness.py
python app/local_multi_doc_smoke.py --model YOUR_INSTALLED_MODEL
```

The smoke uses harmless built-in text samples and requires an **exact quoted fact from the correct second file**, or reports `BLOCKED`. It also measures elapsed time and available system RAM (which is **not** model-specific RAM usage). GitHub CI uses fake model fixtures, so it cannot establish actual model performance on your hardware. [Multi-document limits, evidence rules and device acceptance instructions](docs/LOCAL_MULTI_DOCUMENT_RETRIEVAL_V1.md).

## Improved multi-document retrieval and transparent offline evaluation (Slice 58)

The local multi-document agent now uses **overlapping 450-character source windows (96-character overlap)** instead of nonoverlapping chunks, with limited plural/`-nya` normalization for Indonesian and English question words. It keeps exact file-bound citation validation, but now also shows **why each retrieved passage was chosen** (matched words, original filename, chunk position, bounded source preview). Matched words and quote presence are **not** model confidence or verified factual accuracy.

```powershell
python app/local_retrieval_eval.py
```

This fully offline command compares historical Slice 57 retrieval to Slice 58 over the same **curated synthetic cases** (including an old chunk-boundary failure). Its success means the specified spans were found in the correct file. It is not a real-world benchmark, and it does not prove Ollama is installed on your computer. For the separate Windows **real-model** test continue using `python app/local_multi_doc_smoke.py --model YOUR_INSTALLED_MODEL`. [Read the v2 retrieval trace, measurable comparison and limits](docs/LOCAL_RETRIEVAL_V2_EVALUATION.md).

## One-click actual Ollama acceptance in the localhost UI (Slice 59)

The model compatibility gate is now accessible **directly in the authenticated local workbench**, rather than relying only on synthetic GitHub CI or a manual terminal smoke command. The check runs one real inference on two harmless built-in text samples, verifies the answer **and** an exact citation from the correct source document, and shows the observed model name, elapsed time, and available system RAM before/after (not model-specific RSS).

```powershell
python app/local_server.py --enable-local-agent --enable-multi-documents
```

After local owner login, click **Find installed models**, choose the exact name of a locally installed model, then under **Verify the installed model on this computer**, click **Run real Ollama acceptance on this PC**. This button does not install Ollama, request credentials, read your selected files, save a report, or use cloud AI. If the model is missing, the inference fails, or the response is unsupported, it reports **BLOCKED**.

The UI and test contract explicitly distinguish `LIVE_LOCAL_OLLAMA` from `INJECTED_TEST_TRANSPORT`. A green CI fixture is **not** Windows-device evidence; the operator must run the check on their own PC. The old CLI `python app/local_multi_doc_smoke.py --model YOUR_INSTALLED_MODEL` remains available. [Details, validation and limitations](docs/REAL_LOCAL_MODEL_ACCEPTANCE_UI_V1.md).

## Ollama reliability diagnostics for Windows (Slice 60)

The one-click local-device acceptance introduced in Slice 59 now has a **bounded 90-second Ollama request timeout** for the first-model-load scenario on RAM-limited Windows PCs. Regular chat and other Ollama calls retain the normal 25-second timeout. Timeouts, missing models, Ollama HTTP 404/5xx, invalid JSON, oversize output and incorrect source evidence now produce **separate, actionable error codes** in the local UI. Timeout requests are **never automatically retried**, because the model may still be running after the client gives up.

To test the actual device, start `python app/local_server.py --enable-local-agent --enable-multi-documents`, sign in, select an installed Ollama model and click **Run real Ollama acceptance on this PC**. This is one user-approved local inference, not a cloud call or model installer. Its observed elapsed time and system-wide available RAM do **not** represent model-specific peak memory.

GitHub CI uses injected errors/model fixtures and does **not** establish a genuine Windows Ollama pass. See [the diagnostic code matrix and real device acceptance rules](docs/REAL_LOCAL_MODEL_ACCEPTANCE_UI_V1.md).

## Ambiguous Ollama connection failures (Slice 65)

An Ollama inference request can be **accepted** by the localhost server before its HTTP connection resets, aborts, breaks, or returns a truncated/malformed HTTP stream. Previously these ambiguous failures were reported as generic `local_model_unavailable` and the workbench's durable `IN_FLIGHT` checkpoint could be cleared even though inference may still have been running. The transport now distinguishes **connection refused before dispatch** (`local_model_unavailable`, no quarantine) from an **uncertain transport failure** (`local_model_transport_uncertain`, durable `UNCERTAIN` quarantine). Follow-up inference is blocked with HTTP 423 until the authenticated operator inspects Ollama and explicitly acknowledges the safety state. No automatic retry, model cancellation, download, cloud fallback or new permission is introduced.

The distinction covers interrupted document analysis, model probes, live acceptance, ordinary chats and model-powered read-only task plans. Local CLI/Windows preflight and browser give a safe diagnostic. [Recovery and scope](docs/OLLAMA_CRASH_RECOVERY_V1.md). This is not evidence of a real Ollama run on the owner's PC; actual on-device acceptance remains [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).

## Durable restart and crash recovery (Slice 64)

The model timeout safety latch now persists locally in a minimal SQLite checkpoint (`~/.ai-automation-studio/ollama-safety.sqlite3`). The server commits `IN_FLIGHT` **before** dispatching each model-backed workbench operation. A recognized timeout changes it to `UNCERTAIN`; an unfinished `IN_FLIGHT` record discovered on restart is also treated as `UNCERTAIN`, never assumed completed. New model requests are blocked with 423 until the owner checks Ollama and explicitly acknowledges. The status UI distinguishes `IDLE`, `IN_FLIGHT` and `UNCERTAIN`. Invalid/unreadable safety storage is fail-closed, not silently recreated.

This does **not** track arbitrary third-party Ollama calls or prove the backend has stopped. Deleting the local safety file manually defeats this safeguard; do not delete it to bypass quarantine. [Details, state transitions, tests and recovery limitations](docs/OLLAMA_CRASH_RECOVERY_V1.md). Actual Ollama acceptance on the owner's Windows PC is still unverified ([Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68)).

## Timeout quarantine for real local Ollama requests (Slice 63)

A timed-out Ollama request may still be running in the backend after the HTTP client gives up. A new owner-gated safety latch now blocks further model inferences after an observed timeout, responding with `local_ollama_timeout_unresolved` (HTTP 423) instead of allowing rapid manual retries. Authenticated **Check timeout safety state (no inference)** remains available; an operator must inspect local Ollama before explicitly selecting **I inspected Ollama; re-enable inference**. The acknowledgement requires owner login, CSRF and a confirmation; it **does not cancel Ollama, prove the backend stopped, or execute another inference**. Other local applications and workbench restarts are outside this in-process latch's guarantee.

The feature applies to local chat, document Q&A, model probes, live acceptance, and Ollama-powered approved task plans. Read-only preflight and permitted administrative actions remain separate. This is safety hardening, **not real Windows model evidence**; [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68) remains open. [Operator behavior, HTTP contract and limitations](docs/OLLAMA_TIMEOUT_QUARANTINE_V1.md).

## Evidence check for numeric answers (Slice 62)

Selected single-file and multi-document Q&A now reject a model answer when a literal number, date, time, percentage or compact-unit measurement (e.g. `5kg`, `16GB`) in the answer is **absent from every independently verified exact source quote**. For example, quoting an authentic `18:30 WIB` passage does not justify answering `19:15 WIB`. The check runs only after the existing file/chunk/character quote validation; it does not create a new inference, inspect additional files or persist data. Blocked outputs use `document_numeric_evidence_missing` or `multi_document_numeric_evidence_missing` (HTTP 502 for unsupported model output).

This is deliberately a conservative **literal** check. Equivalent numeric formats may be rejected (e.g. dates written differently). It is **not semantic entailment**: a model can still misuse an authentic quote, negate the source, or fabricate an unsupported non-numeric claim. The result continues to display `interpretation_verified: false`. Deterministic regression tests demonstrate the previously admitted unsupported-number case. Real inference on the owner's Windows PC still requires the separate manual gate in [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).

## One-click Windows host prerequisites and optional live model evidence (Slice 61)

AI Automation Studio now distinguishes a safe **read-only preflight** from actual Ollama inference. This is useful before trying to run local models on constrained Windows hardware: the preflight checks only fixed-localhost Ollama model names and current system RAM, without loading model weights, scanning files, downloading models or modifying anything.

```powershell
python app/local_windows_preflight.py
python app/local_windows_preflight.py --model YOUR_INSTALLED_MODEL
```

The preflight reports whether Ollama is reachable, models are installed, the requested model exists, and how much **system-wide** RAM is currently available. It does **not** prove the model can run. The **explicit** one-request genuine Ollama source-citation challenge is:

```powershell
python app/local_windows_preflight.py --model YOUR_INSTALLED_MODEL --run-smoke
```

Alternatively, start `python app/local_server.py --enable-local-agent --enable-multi-documents`, authenticate at `http://127.0.0.1:8765`, then click **Check Windows & Ollama readiness (no inference)**. The existing **Run real Ollama acceptance on this PC** button is separate and actually invokes Ollama only when clicked. Missing models, unreachable API, low memory warnings and real model failures are kept distinct. **CI mocks do not count as successful Windows-device acceptance.** [Read the complete operator preflight guide](docs/WINDOWS_OLLAMA_PREFLIGHT_V1.md).

## Run the demo

Requires Python 3.11+ with SQLite available. No external provider credentials or API billing. Starting the server creates a local database at `~/.ai-automation-studio/local-workflows.sqlite3` for rule workflows and pseudonymous event history; it does not persist the five mock example runs.

**Slice 43: one-time local operator setup is mandatory before the normal server will bind.** Run in a terminal on your own computer:

```bash
python app/local_server.py --init-owner
python app/local_server.py
```

The first command prompts securely for a **12–128-character password** and confirmation, stores only a salted scrypt verifier at `~/.ai-automation-studio/operator.json`, and refuses to overwrite an existing owner. **Do not delete the owner file casually: optional one-time offline recovery is available only if enrolled first (see the recovery section below).** The second command loads that identity and fails closed when it is absent or invalid. Open **http://127.0.0.1:8765**, enter the password on the local login screen, and use **Lock session** to revoke the session.

The server binds only to `127.0.0.1`. Sessions are one-hour, short-lived process-memory tokens in HttpOnly, SameSite=Strict cookies; every mutation requires the matching session CSRF token, including old mock APIs and GitHub source preview. No real user accounts, cloud provider credentials, outside identity provider or GitHub login are created. Do not expose this unauthenticated-at-the-network-layer local HTTP service using public proxies or port forwarding. It remains unsuitable for real customer data or a compromised local PC.

## Security audit and uncertain-write containment (Slice 46)

The local server now stores a **redacted, bounded SQLite security event log** at `~/.ai-automation-studio/security-audit.sqlite3`. Sign in and open **Recent security events**, or use authenticated `GET /api/operator-audit?limit=40`. Entries show event type, result, UTC timestamp and at most a hashed public-source fingerprint. **No passwords, recovery codes, cookie values, raw repository names, GitHub issue text or customer data** go into the ledger. Failed audit storage blocks subsequent sensitive actions rather than silently losing evidence. It is not tamper-proof against the same Windows/OS account.

Sensitive owner and public-source file changes now distinguish **pre-rename failures** (old file intact) from **post-rename directory-sync failures** (new on-disk state may already be visible). A post-rename failure **revokes the current operator sessions** or **denies all public-source permissions** and requires restarting the local server to reload the actual on-disk state. Neither the old nor new password/source grant should be assumed to have persisted after a 503 restart-required error.

On Windows, use the optional **read-only** ACL inspection from the cloned project:

```powershell
powershell -NoProfile -File .\scripts\check_windows_local_acl.ps1
```

This reports broad file/directory access without reading file contents or changing permissions; it has not yet been verified on a real Windows installation. See [local security durability and audit contract](docs/LOCAL_SECURITY_DURABILITY_AUDIT_V1.md). The app is still localhost-only and **not approved for real customer PII or public hosting**.

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

The local owner can now use **Change operator password** to rotate the credential after proving knowledge of the current password. On successful atomic verifier replacement, **all operator sessions are revoked immediately**; sign in again with the new password. A storage failure leaves the previous credential and sessions unchanged. **Offline one-time recovery is documented above**; don't remove the owner file or assume there is an anonymous reset route.

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


### Reviewed read-only plan execution (Slice 72)

After proposing and approving an explicitly reviewed 1–3-step task plan, use **Run remaining reviewed read-only steps** to confirm the exact remaining tools/arguments once and execute them sequentially in the open browser. **Stop after current step** prevents additional dispatches but cannot cancel an in-flight request; failures never auto-retry. All actions still pass existing server authorization, source checks and persisted state checks, and final report generation remains separately owner-triggered. See [reviewed batch run safety and limits](docs/REVIEWED_READONLY_BATCH_RUN_V1.md). This is not a background autonomous agent. No Windows/Ollama device acceptance is claimed.

### Remote job eligibility *evidence* triage (Slice 81)

An existing explicit owner-clicked Greenhouse public ATS GET now includes a conservative, quote-backed deterministic job wording assessment. It distinguishes **work location, work authorization, remote/hybrid signals, and explicit experience years**, while retaining the original *Indonesia eligibility not verified* flags. A restrictive foreign residence phrase is surfaced for manual review, never treated as an autonomous job rejection. The tool does not auto-apply, rank or send applicant data. See [Slice 81 contract and caveats](docs/REMOTE_JOB_ELIGIBILITY_EVIDENCE_V1.md).
