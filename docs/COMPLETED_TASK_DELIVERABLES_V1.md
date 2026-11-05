# Slice 68 · Completed task deliverables

## What it adds

A durable owner-approved task plan previously finished as a collection of raw tool results. This feature adds an **explicit final deliverable** after those read-only steps are complete:

1. Create and approve a bounded task plan as before. Execute every read-only step by pressing **Execute next step** separately.
2. Only after the plan reaches **COMPLETED**, click **Generate final report**. This makes **one new local Ollama request**, using a bounded excerpt of each previously saved tool output. It neither re-executes plan steps nor fetches external sources.
3. The model must return short structured JSON: a summary and one to three findings. Each finding includes a 1-based step number, a verbatim quote from that step's recorded output, and a clearly marked **unverified interpretation**. The server checks exact quote membership against the text that was provided to the model and rejects fabricated/wrong-step citations. This does **not** prove the interpretation itself is accurate.
4. The verified-shape report is written once to the **existing local plaintext SQLite plan ledger**, and can be opened again through **Load plan** without another model call. Repeat generate requests return the stored result, not another inference.
5. The authenticated owner may use **Copy report** or explicitly **Download .md** in the browser to create an editable Markdown deliverable. The app does not automatically publish, send, or attach it elsewhere.

### Run

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans
```

Optional `--enable-agent-memory` still supports owner-selected notes during proposal, and optional `--enable-agent-github-read` still controls public GitHub issue data in plan steps. This feature **does not enable either flag automatically**.

### API

```http
POST /api/local-agent-plan-report
{"plan_id":"EXISTING_24_CHAR_LOWERCASE_HEX"}
```

All calls require the existing local operator session, POST-CSRF, loopback/same-origin checks, and durable Ollama in-flight/quarantine safeguards. The plan must be `COMPLETED` (otherwise `409 plan_report_requires_completed`). The existing `GET /api/local-agent-plan?plan_id=...` returns its `report` field or `null`. Failed/malformed or unsupported model reports are not stored, and no fake or wrong-step citation is accepted. Model timeout/uncertain transport still triggers durable quarantine. The generated report is **not** editable by model tools.

### Limits and trust

- For each saved step, the local model sees **at most the first 3,500 characters of the recorded tool output**. Only those excerpts are available for report citations; longer source outputs may be incomplete. No local file crawling or additional third-party fetch is performed.
- A report is bounded to a 700-character summary, 1–3 findings, interpretations of at most 240 characters each and literal quotes of at most 180 characters each (including short numeric calculator outputs). The request uses a fixed JSON-format model call with bounded prediction length.
- `evidence_membership_verified: true` means the literal cited characters occur in the correct recorded output excerpt. It **does not** mean the AI summary/interpretation is fact-checked or that the underlying GitHub labels or owner-supplied data are true.
- Tool results and any model-generated text are **untrusted data**; they do not expand tool permissions, and no model-generated links are followed. The browser displays report text as text, not rendered HTML. This is **not** authorization for model-directed file modification, email, system shell commands or unrestricted agent operations.
- No content is sent to a cloud model; the report call is to local Ollama using the existing loopback transport. The user explicitly deferred Windows/Ollama real-device testing. This release does not prove any specific installed model will produce the required JSON on the user's PC.
- The plan ledger, including report, may contain unverified or sensitive model observations as **plaintext SQLite**. Do not use it for credentials, customer PII or sensitive records. Deleting the plan may not securely erase copies from OS backups or SQLite freed pages.
- Older plan databases migrate by adding a nullable-in-practice `report_json` column with an empty-string default. Old saved plans retain goals, steps, approvals, results and completion state; already completed old plans may request a report without rerunning steps.

### Verification policy

GitHub Actions tests cover model protocol parsing with **injected synthetic Ollama responses**, exact-source-quote validation, SQLite migration/restart, HTTP owner/CSRF behavior, and Chromium browser UI including Markdown download. These are **not** real on-device model tests. The user's build-first decision keeps [Issue #68: real Windows/Ollama acceptance](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68) deferred until the core product is complete.

Also see [Plan execution](LOCAL_AGENT_TASK_PLANS_V1.md) and [Memory-backed plans](MEMORY_BACKED_AGENT_PLANS_V1.md).
