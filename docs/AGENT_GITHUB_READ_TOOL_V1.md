# Slice 53 · Permissioned live public GitHub issue reads inside agent task plans

AI Automation Studio remains an **open-source, single-operator, localhost-first agent**, not a SaaS. Slice 53 adds the first **real external read-only tool** to the Slice 52 durable task-plan executor. It retrieves bounded issue metadata from the public GitHub REST API, not a fake issue source. The model proposes a read, but explicit operator permissions and plan approval determine whether and when the HTTP GET happens.

## Opt-in setup

From the repository root on the operator's machine:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-agent-github-read
```

The three flags are required to enable the GitHub plan tool. Ollama must be running separately with a compatible installed model. The external source tool is **disabled by default**; `--enable-agent-github-read` without task plans is rejected. `--enable-agent-history` is unrelated and optional.

Sign in at `http://127.0.0.1:8765`. In **Public Source Permissions**, grant the exact public repository `Octocat/Hello-World` using the existing owner password confirmation. The default source allowlist is empty, and granting a repository only authorizes this bounded public read and the older expressly selected source workflow, **not** GitHub writes or private access.

In **Ask your local AI agent**, enter your installed model name. In **Plan a small local task**, use a goal such as:

> List the open GitHub issues from Octocat/Hello-World.

The model is asked for JSON steps and may suggest `github_public_issues` with the exact arguments `{"repo":"Octocat/Hello-World"}`. Invalid, unsupported or unauthorized plans are rejected. Review the precise tool and repository in the `PREPARED` plan, select **Approve reviewed plan**, and separately select **Execute next step**. Only that final step sends the public HTTPS GET. Neither proposal nor approval fetches GitHub data.

## Strict data contract

The reader reuses the existing hardened `github_public_issue_intake._public_issues_http` connector, which uses a fixed verified TLS connection to `api.github.com:443` with an unauthenticated `GET /repos/{owner}/{repo}/issues?state=open&per_page=15&page=1`. It does **not** follow HTTP redirects, accept arbitrary hosts or URLs, use API tokens or call mutating endpoints. Request timeout is 5 seconds; provider body maximum is 512 KiB.

A successful result is a bounded serialized JSON string stored as the step output. It includes:

- `source: GITHUB_PUBLIC_ISSUES_READ_ONLY`, the specific `repository`, and `fetched_at_utc`
- `page_limit: 15`, `returned_count`, and a conservative `may_have_more` indication
- For each open non-PR issue: positive integer `number`, `title` capped at 160 characters, at most five short labels, and a URL constructed from the validated repository and issue number
- `external_read_count: 1` and `external_write_count: 0`

Pull requests, issue bodies, author identities, comments, private repository content, attachments and arbitrary provider fields are excluded. The list contains **at most the first 15 fetched entries** and is not an exhaustive repository audit. GitHub's issues endpoint can contain pull requests; those entries are filtered. Treat public issue titles and labels as *untrusted external data*, not instructions. The executor **does not automatically pass tool outputs back into the model** or give external content new permissions.

## Approval and revocation semantics

The source policy is the already established `LocalPublicSourcePolicy`, with exact-repository grants and a durable policy epoch. Permissions are checked locally at plan creation, again on approval, and before and after the actual read.

At approval time the current policy epoch is saved in the plan's `approval_epoch` field. A source-policy version change, including **revoke → regrant**, invalidates the old approval for GitHub reads. A denied next-step request returns `public_source_permission_changed` without contacting GitHub. Creating a new plan and approving it again is required. This is intentionally conservative: changing an unrelated source grant may also invalidate existing GitHub plan approvals. Reads hold the policy's lock during the bounded network request, so concurrent grant changes wait for the read to complete.

The plan state is still durable and manually controlled: `PREPARED → APPROVED → NEXT_READY/COMPLETED`. An interrupted step is not automatically replayed. If the external provider fails **after** the in-flight marker, the plan enters `BLOCKED` and cannot automatically retry; the owner may delete the terminal plan and create a new one. This is not a cross-process distributed lock or a durable remote-job queue.

## Data storage and migration

A nullable `approval_epoch INTEGER` is added **in place** to the existing `~/.ai-automation-studio/agent-task-plans.sqlite3` schema. Existing plans remain readable and retain their status; no old steps are automatically executed. The local SQLite file is plaintext and may include public issue titles, repo names and user-entered goals. Avoid private/customer data or secrets and do not assume forensic secure erasure.

The original calculator and UTC clock remain available. GitHub steps cannot execute when the GitHub reader flag is off, even when a plan created earlier is present; the owner can still view and cancel the plan.

## Verification boundary

Unit and HTTP tests validate exact allowlists, title sanitization, no provider call before approval, single authorized read, CSRF and session checks, source revocation (including revoke/regrant), default deny, provider error handling and in-place migration. Chromium exercises the actual browser review/approval/dispatch UI with a **deterministic injected public GitHub response**, not live provider data.

Existing GitHub CI checks perform actual **public read-only GitHub API integration** on their own test paths, but do **not** establish that this new agent plan tool was exercised end-to-end with a real installed Ollama model and live GitHub issue data on the user's Windows computer. That requires a separate user-run acceptance check. The previously documented real-model readiness probe still applies.

**Not supported:** issue creation/editing, private GitHub repositories, GitHub OAuth tokens, issue body extraction, arbitrary browsing, agent-managed credentials or automatic write-capable actions. The first useful external tool is deliberately narrow, not an excuse to silently give a model general network access.
