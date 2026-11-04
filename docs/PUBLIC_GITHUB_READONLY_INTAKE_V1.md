# Public GitHub Issues Read-Only Intake · Slice 40

Date: 2026-11-04. Scope: **first network-capable source adapter**, public GitHub Issues API only, bounded one-time read, explicit local SQLite import. This is NOT an authenticated production connector, automatic subscription, webhook, CRM integration or AI agent.

## Why this provider and what the user actually gets

The existing local rule engine already accepts allowlisted service/budget/timeline categories. GitHub publicly exposes labeled issue metadata through the official REST `GET /repos/{owner}/{repo}/issues` endpoint without authentication for public repositories. This adapter uses that official API as a real external read-only source, avoiding credentials and unrelated accounts.

**What happens when the user clicks Read public issues & preview locally:**

1. The operator first creates and activates a local workflow, then enters a **public** GitHub `owner/repo`. Custom URLs, ports, schemes and credentials are not accepted.
2. The server checks the workflow is ACTIVE before contacting GitHub. It performs a **single HTTPS GET** to `api.github.com:443` using the exact path `/repos/OWNER/REPO/issues?state=open&per_page=15&page=1`, a five-second timeout, verified TLS, an explicit API version header and **no Authorization header**. It rejects redirects instead of following them.
3. The provider may include issue titles, descriptions, commenters and user profiles in its response. The adapter **does not use or persist those fields**. It reads and maps only the issue's numeric ID, open status, pull-request marker and the three explicit label categories below.
4. PRs, closed/malformed records, missing/ambiguous labels and extra/unrecognized category values are ignored, not guessed. Results are shown in a no-write preview. **GitHub is never modified**.
5. On explicit **Import reviewed issues to local SQLite**, an in-memory snapshot is consumed once and validated through the same Slice 38 SQLite batch commit mechanism. The snapshot has a five-minute lifetime and the existing local active-workflow version, control epoch, ledger-count and payload digest gates. Duplicate events replay the original outcome. New events are written together or not at all.

The `GET` response is bounded to **512 KiB**, one page of at most **15 items**. If the first page contains no eligible issues, the UI displays `NO_MATCHING_PUBLIC_ISSUES`, which **does not establish** that the entire repository has no matches. There is no paging or background polling.

## Explicit GitHub issue-label mapping

One and only one from each of the following groups is required:

- Service: `aas:service:automation`, `aas:service:integration`, or `aas:service:consulting`.
- Budget tier: `aas:budget:1`, `aas:budget:2`, or `aas:budget:3`.
- Timeline: `aas:timeline:urgent`, `aas:timeline:soon`, or `aas:timeline:flexible`.

These labels must already exist and be attached to public issues by the repository's maintainers. This prototype **does not create labels or modify issues**. Labels express the source owner's declared categories; no AI inference is used. A random third-party repo is unlikely to have this schema.

For accepted issues the event ID is `gh_<first-12-chars-of-SHA256(lowercase-owner/repo)>_<issue-number>`. This stable, repository-scoped ID avoids storing owner names or issue titles in the durable event ledger. The number itself is public; the ID is pseudonymous, not a guarantee of anonymity. The categories and deterministic rule outcomes are stored in the existing local SQLite history.

## Endpoints

- `POST /api/local-github-preview`: request `{"workflow_id":"example_flow","repo":"owner/public-repo"}`. Returns sanitized mapped outcomes, scanned/ignored counts, source label, optional one-time snapshot ID and fetched timestamp. Does **not** write local events.
- `POST /api/local-github-commit`: request `{"workflow_id":"example_flow","snapshot_id":"<one-time-preview-id>"}`. Consumes the ephemeral snapshot and commits its prevalidated, fixed categorical projection through `LocalCSVBatchIntake.commit` if workflow and ledger still match. A failed or expired snapshot requires fetching again.
- Both endpoints retain the local server's Host, Origin, JSON, request-size, no-store and loopback gates. Those are **not equivalent to authentication**.

The snapshot is **not an authorization credential tied to a verified human identity**. It only demonstrates explicit same-machine UI intent. Restarting the Python server discards all snapshots, while already committed SQLite events remain.

## Implementation and limits

- `app/github_public_issue_intake.py`: fixed-host standard-library HTTPS transport, strict repo path grammar, TLS verification, no redirects, redacted HTTP/network errors, schema sanitizer, deterministic mapping, five-minute snapshot cache and explicit one-time local commit.
- `app/local_server.py`: two new allowlisted localhost HTTP endpoints with injected fetcher/clock in tests, live default standard-library HTTPS transport when the operator clicks Preview.
- `web/index.html`: explicit source preview and local commit controls. No content, personal data, access tokens or snapshots in browser storage. Changing repo/workflow, or refreshing local workflow status, invalidates any cached preview.
- `tests/test_github_public_readonly_http.py`: isolated end-to-end local HTTP/SQLite connector contract with **synthetic provider responses**, including host fixation, no auth, redirect rejection, redaction, source item filtering, preview/commit, expiry, concurrency, pause and history.
- `tests_browser/test_real_browser.py`: real Chromium tests of source input validation and pause gates **without accessing external GitHub**.

### What is actually verified

The official public Issues endpoint, unauthenticated eligibility, and the fact that Issues endpoints may also return pull requests are documented by [GitHub REST API: List repository issues](https://docs.github.com/en/rest/issues/issues) and [GitHub REST API: labels](https://docs.github.com/en/rest/issues/labels). Tests use dependency-injected synthetic HTTP responses and mock the low-level HTTPS transport. **They do not establish that live GitHub responded, that a specific repository has the required labels, or that ingestion worked against a real public repository on a network.** A separate real-network manual smoke with an appropriately labeled public repo is needed before describing the provider as `LIVE_VERIFIED`.

This release is an **implemented live-capable read-only adapter**, but its real provider E2E status remains `LIVE_NOT_VERIFIED`. Never confuse simulated provider tests with external integration acceptance.

## Security and product blockers

The local development HTTP server has no login, user identity, multi-tenant authorization, or application-level database encryption. Other local processes may be able to invoke the service. Do not expose the server to LAN/public internet, do not point it at private repositories and do not process real customer data. Reading GitHub public metadata does not establish permission to republish or process personal information.

An unauthenticated public API may rate-limit or reject requests. The adapter fails closed with generic provider codes; it does not ask for secrets or retry aggressively. The source is one page of open issues, no pagination, no scheduled polling, no subscribed webhooks and no GitHub writes. Label changes after preview are not refreshed at commit: an approved snapshot is deliberately frozen for five minutes and locally revalidated only against current workflow/ledger. Re-read to get more recent GitHub state.

Next gates: verify an actual labeled public repo over the network, formalize source permissions and provenance, and implement authenticated identity before handling private or customer data. Provider writes or unattended automation require separate approvals, reconciliation and audit.
