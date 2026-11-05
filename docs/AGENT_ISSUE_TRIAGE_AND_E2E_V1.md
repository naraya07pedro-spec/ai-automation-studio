# Slice 54: source-backed public GitHub issue triage and real-host acceptance

AI Automation Studio remains **local-first, open source, single operator**, not a hosted SaaS. Slice 54 adds a useful report from a real approved GitHub source while retaining explicit source grants, human review and one-step tool execution.

## User workflow

1. Start the workbench with all three optional features: `python app/local_server.py --enable-local-agent --enable-agent-plans --enable-agent-github-read`. Install/start Ollama separately with a compatible model.
2. Sign into `http://127.0.0.1:8765`; under **Public Source Permissions**, allow the exact public repository using the owner-password confirmation.
3. In the local agent panel, choose an installed model. In **Plan a small local task**, enter: `Triage open GitHub issues from Octocat/Hello-World using their labels` (replace with your granted public repository).
4. **Propose** a plan. Review the proposed `github_issue_triage` step and exact `{"repo":"owner/repo"}` arguments. Creation does not fetch GitHub issues.
5. **Approve reviewed plan**. This commits an explicit permission-bound approval, but still does not fetch. **Execute next step** once to read the repository's first page of open public issues through fixed-host TLS GitHub API.
6. Read the source-backed report, then inspect linked issues yourself before assigning a human impact/severity. The report remains in the selected local plan until you delete the terminal plan.

The new tool is `github_issue_triage`. Existing `github_public_issues` remains unchanged. Neither tool can create, comment on, label, close, or otherwise edit any GitHub issue.

## What the report actually supports

The report is **deterministic label classification, not LLM-derived issue severity**. It contains `source: GITHUB_PUBLIC_ISSUE_TRIAGE_READ_ONLY`, `method: DETERMINISTIC_LABEL_RULES_V1`, repository name, actual fetch timestamp in UTC, sample-size boundary and URLs generated from validated repository name and positive issue numbers.

Issue categories are based on explicit case-insensitive, exact labels: bug (`bug`, `type:bug`, `kind:bug`), documentation (`docs`, `documentation`, `type:docs`), feature (`enhancement`, `feature`, `type:feature`), and maintenance (`maintenance`, `chore`, `type:chore`). If no recognized category label is in the first five sanitized labels, the issue is **unclassified**.

`REVIEW_FLAGGED` means at least one recognized visible priority/review label exists: `p0`, `p1`, `priority:critical`, `priority:high`, `critical`, `urgent`, or `security`. It does **not** establish actual urgency or vulnerability. Otherwise an issue is `UNASSESSED`, **never assumed safe/low priority**. The report includes the exact matching evidence labels, reason, category counts, flagged/unassessed counts and per-issue source URL. Flagged entries are listed before unassessed entries; numeric issue numbers provide deterministic tie-breaking.

Only the initial GitHub API page (max 15 entries before PR/duplicate filtering) is used; titles are truncated at 160 characters and labels at 5 values of max 40 characters. Bodies, comments, author identities, attachments and private repositories are excluded. The report explicitly states `may_have_more` and does not pretend to represent all open issues or their operational/business impact. The label categories are a review aid, not a guarantee; a title containing alarming wording does not create a priority finding without evidence.

**Untrusted data boundary:** public titles and labels are displayed as text and preserved for source review, not injected back into an LLM as instructions, executed as code, or interpreted as permission changes.

## Real Ollama + GitHub smoke on Windows

A new opt-in CLI offers a **real end-to-end acceptance command on the operator's own PC**. It does not bypass the repo allowlist or silently contact GitHub.

Preconditions: Python installed, Ollama running locally on port 11434, a JSON-plan-capable model installed, and `Octocat/Hello-World` (or the chosen public repo) already granted using the workbench owner login. The local model-name check runs first.

```powershell
python app/local_ollama_readiness.py
python app/local_agent_e2e_smoke.py --model qwen2.5:3b --repo Octocat/Hello-World
```

The `qwen2.5:3b` name is **illustrative only**. Replace it with an actually installed, suitable Ollama model. The command verifies the selected model exists in `/api/tags`, asks the **real** Ollama `/api/chat` to propose a JSON plan, and accepts **only exactly one** `github_issue_triage` step for the selected granted repo. It creates a plan in **ephemeral in-memory SQLite**, validates the result, and prompts:

```text
EXECUTE Octocat/Hello-World
```

Only typing this exact phrase permits a single real public GitHub GET. Otherwise the operation ends without calling the GitHub provider. Results are printed to the terminal as structured JSON, including actual model identifier, source, read/write counts, category evidence and issue URLs. The command does not store the plan or report on disk or send source content back into Ollama. It does read the already provisioned plaintext source grant policy file. No personal access token, cloud AI key, or account subscription is required.

If the model outputs a malformed/wrong/multi-step plan, the command **fails closed** instead of fabricating compatibility. Real success must be supported by the actual command output on the user's Windows machine, not assumed from CI.

## Engineering verification

Python and authenticated HTTP tests cover provider allowlist, source-policy epoch revocation, approval gates, deterministic classification and evidence labels, no issue body/author leakage, untrusted misleading titles, and the acceptance CLI's exact typed consent. Chromium acceptance exercises the actual browser report using an **injected fake model and GitHub fixture**. Existing independent CI jobs make real public GitHub read-only requests on their own adapter paths; they do not constitute a real Ollama + triage plan end-to-end acceptance.

Risk boundaries: SQLite task history is plaintext; no forensic secure deletion. Model performance/compatibility on Windows remains unverified until the user runs the smoke. GitHub rate limits and empty results are legitimate outcomes. This feature is not an autonomous security scanner, exhaustive issue audit, live backlog SLA monitor, or GitHub write assistant.
