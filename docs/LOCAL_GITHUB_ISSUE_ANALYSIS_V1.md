# Slice 55 · Ollama analysis of real public GitHub issue metadata with source links

AI Automation Studio remains an **open-source, single-owner, local-first agent**, not a hosted SaaS. Slice 55 introduces an actual local-model synthesis step over public GitHub issue metadata rather than only the deterministic label rules from Slice 54.

## Workflow and opt-in

Run with the same explicit three permissions that were required in Slices 53 and 54:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-agent-github-read
```

You must also start Ollama separately, install a JSON-output-compatible model and grant a **specific public repository** under the authenticated **Public Source Permissions** controls. The default allowlist is empty. This agent never downloads or installs a model by itself.

In **Ask your local AI agent**, enter an installed model name. In **Plan a small local task**, enter:

```text
Analyze open GitHub issues from Octocat/Hello-World and suggest review questions
```

The model may propose a `github_issue_analysis` step with exactly `{"repo":"Octocat/Hello-World"}`. You must inspect the exact tool and source, click **Approve reviewed plan**, and separately click **Execute next step**. Proposal and approval only validate permission locally and **do not fetch data or call the analysis model**. The approved step then runs one fixed-host, unauthenticated public GitHub GET and a separate Ollama `/api/chat` JSON-format inference, returning an evidence-linked report.

## Exact claim and evidence boundaries

The source adapter retains at most the first 15 open public issue entries, excluding pull requests and duplicates, from GitHub's fixed `/repos/owner/repo/issues?state=open&per_page=15&page=1` endpoint. Metadata passed to Ollama is restricted to **public issue number, truncated title, and up to five short labels**, plus the selected repository name and current user review objective. Issue bodies, comments, authors, tokens, hidden data, stored conversations, filesystem contents and previous task outputs **are not supplied**.

The model is called **without tools or external-action permissions**; it must return exact JSON `{"items":[{"number":42,"observation":"..."}]}`, selecting 1–5 distinct issue numbers that are present in the fetched snapshot. The validator rejects invented IDs, invalid schema, duplicate IDs, excessive length and oversized outputs. Issue titles, labels and canonical URLs in the final report are reconstructed from the **source snapshot**, not trusted model-provided text. Each model observation is labelled `UNVERIFIED_MODEL_INTERPRETATION` and shown as a review question, **not** a verified bug, severity score, root cause, attack finding, solution or business consequence.

The JSON result includes the model name, source repository, UTC fetch timestamp, the number of sampled and analyzed issues, original issue links, first-page sampling disclosure, counts of one GitHub read and zero external writes, and explicit limitations.

GitHub issue titles and labels are untrusted content. The local model is instructed to treat them as inert data, not commands. This prompt alone is not an absolute security guarantee; the actual **code-enforced boundaries** are no model tool access, exact response schema, citation membership, fixed provider host, no sensitive inputs and human-approved read-only execution. Text is rendered into the browser with `textContent`, never interpreted as HTML. A model may still produce a misleading interpretation within the allowed observation field; human review of each linked issue remains necessary.

If the GitHub source policy is revoked or its version changes during inference, the final report is rejected and the task is marked blocked. An unauthorized or malformed model step never gains new access by mentioning a URL or modifying the model response. Failed steps do not silently retry.

## Persistence and constraints

The task plan ledger stores the goal, approved steps, model-generated questions and source metadata **in plaintext SQLite** at `~/.ai-automation-studio/agent-task-plans.sqlite3`. This is not encryption or secure erasure. Do not use secrets, customer PII or internal issue content. The model is called against fixed localhost Ollama and no cloud model keys are required; model weights, compute and actual runtime reliability depend on the installation.

At most 12 stored plans, 3 steps per plan and one local agent operation in flight are retained. An issue analysis step produces at most **five** observations. A source fetch might return zero issues; in that case the result reports `NO_ISSUES_TO_ANALYZE` and **does not invoke Ollama**. GitHub paging, search over multiple repositories, private access and issue modifications are out of scope. The analysis tool shares the existing grant/approval epoch and one-step-per-click requirements with `github_public_issues` and `github_issue_triage`.

## Tests and real-device gate

Unit tests check source-only citations, prompt injection attempts, no issue body/author transfer, empty issue responses, consent and policy revocation, schema refusal and durable-plan approval semantics. HTTP integration tests verify the owner session and CSRF gates. Chromium acceptance clicks the real proposal/approval/execute UI but injects a **fake model and fake public GitHub response**. Independent CI jobs read actual public GitHub data through existing adapter tests, but do not prove a real Ollama model completed this combined analysis on the user's Windows computer.

**To establish real-device evidence:** install an appropriate model, grant a public repository, run the above server and execute the approved `github_issue_analysis` plan in the browser. Record the real model name, outcome, exact source links, elapsed time, and memory/timeout observations from that Windows host. A green GitHub CI alone is not an Ollama compatibility certificate.

This is a tightly scoped, source-linked **AI review aid**, not an automated prioritization authority or autonomous GitHub agent.
