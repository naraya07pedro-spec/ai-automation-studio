# Slice 78 · Owner-granted Hacker News public story connector

## Delivered workflow

A local, password-authenticated owner can grant **one exact public Hacker News story/job ID** and then ask their installed local planner to propose a bounded `hn_public_story` step. This reads real public metadata from an **entirely separate external provider** (Hacker News' official Firebase-backed v0 API), unlike the already implemented GitHub public issue connector. It saves the sanitized output and provider provenance to the existing reviewed local task plan. Source bodies and comments are **not** sent to the model during planning; the planner sees only the granted integer IDs.

Enable the optional connector only with:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-hn-story-read
```

In the authenticated workbench, under **Hacker News public story connector**, enter an integer story ID, re-enter the current owner password, and click **Grant exact story ID**. Then create a goal like `Read the owner-granted Hacker News story 8863`, choose the local model, inspect the proposed exact `{"tool":"hn_public_story","args":{"story_id":8863}}` step, approve the plan, and separately click **Execute next step** or authorize the existing bounded read-only batch. Nothing gets fetched simply by granting, proposing, or approving.

## Why this external source

Hacker News' [official public API reference](https://github.com/HackerNews/API) describes `GET https://hacker-news.firebaseio.com/v0/item/<id>.json` for stories, comments and job items. Story `8863` is an official documented example (a historical Dropbox discussion), **not a current job listing**. This integration is a technical read-only data bridge and possible public research source, **not** verified remote-job discovery, eligibility checking or applicant tracking.

## Explicit security contract

- Separate **opt-in** CLI flag `--enable-hn-story-read` requires both agent plans and the local agent. An additional `HNStoryPolicy` in `~/.ai-automation-studio/hn-story-grants.json` is default-deny and holds at most **12 exact integer IDs**, plus a monotonic **grant epoch**. No HN login, API key, token or OAuth secrets.
- Grant/revoke endpoints `POST /api/local-hn-story-grant` and `POST /api/local-hn-story-revoke` accept **exactly** `{"story_id":<positive integer>,"current_password":"..."}` and require valid owner session + CSRF + same-origin/loopback host + current password re-authentication. `GET /api/local-hn-stories` is session-only, not publicly accessible. Security audit records only grant/revoke action, outcome and timestamp, **never password, story content, or grant ID**.
- A plan may include `hn_public_story` only if its ID was already granted by the owner and the feature was explicitly enabled. Creation rechecks current grants before saving. **Approval** persists the exact HN policy epoch in a new backwards-compatible `hn_approval_epoch` SQLite column, separate from GitHub's existing approval epoch. **Execution and manual resume** recheck that exact epoch. Any grant/revoke, even revoke-and-regrant of the same ID, invalidates older approvals. There is no automatic reapproval or retry. The read-only server batch uses the same executor checkpoint.
- At most **3 HTTPS GET requests per approved step** to the one fixed origin `hacker-news.firebaseio.com`, fixed `/v0/item/<integer>.json` path. The reader uses the Python standard library with CA-verified TLS, 6-second timeout per GET and 16-KiB max response per item; it never follows HTTP redirects (non-200 is rejected), reads arbitrary user-provided URLs/hosts, or sends request tokens/credentials. The second and third GETs may fetch at most **2 directly listed top-level comment IDs**, each verified to have `parent` equal to the approved story. No recursive traversal, search, comment scraping of entire threads or automatic polling.
- Only `story` or `job` item types are accepted for the root. Missing, malformed, mismatched, dead or deleted roots are rejected. Comment IDs/types/parents must match. HTML is transformed into bounded plain text, with control characters stripped. The persisted record includes fixed provenance: original story ID, canonical HN item URL, sanitized title/excerpt, at most two comments, UTC created/fetched timestamps, source SHA-256, approval epoch, and explicitly zero external writes/model calls during tool execution.
- Provider text is **untrusted data**, not instructions. Story/comments do not become model planning tools, new permissions or executable code. The output is persisted in the owner-local plan SQLite and may contain unverified public prose or contact information. Do not treat it as authoritative, independently verified, privacy-safe or relevant to hiring without further checks.

## Verification and known limits

- Synthetic Python tests: default deny, exact grants and re-authentication, old-approval revocation, separate epoch persistence and restart, malformed IDs/shapes/child relationships, deterministic provenance and sanitized output, strict fixed origin/HTTP method and noninteractive query, zero model prefetch.
- Browser acceptance: owner password grant, incorrect credential denial, plan creation with **no item body in the planner**, explicit approval and execution, persisted source provenance, and revocation blocking the next reviewed step.
- Disposable Windows: source policy persistence and bounded execution tests. Separate GitHub Actions live test invokes the **official** provider's real public story ID 8863 using only HTTPS GET, no credentials or modifications. Network/API failures can still happen outside CI.
- This **is not** an HN account connector, general HTTP client, arbitrary page fetcher, private SaaS integration or verified remote jobs pipeline. It does not discover all job posts or check Indonesia work eligibility, hiring freshness, employer trustworthiness or dates. Such intelligence requires independent source verification and job filtering.
- The original decision to defer real Windows/Ollama acceptance remains in [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).

Next meaningful expansion: grounded discovery of a small set of candidate public HN hiring thread IDs **without automatic grants**, and separate eligibility verification from employer ATS pages, while retaining manual per-resource approval.
