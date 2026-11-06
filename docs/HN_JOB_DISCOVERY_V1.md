# Slice 79 · Owner-clicked discovery of recent public Hacker News job titles

## Core workflow

This adds **source discovery** to the previously built grant-bound HN story connector, without broadening the local model's privileges. An authenticated owner may explicitly press **Preview latest 5 HN job titles (read-only)** to fetch a few recent *unverified public* job items from the official Hacker News API. The owner sees a timestamp, titles, canonical HN links and IDs, then can **copy one selected ID into the existing grant form**. Copying never grants permission; the owner must still enter the current local password, grant that exact story ID, separately propose/review/approve the plan, then manually execute `hn_public_story` or authorize a bounded read-only batch.

This is **not** a remote jobs engine, ATS verifier, Indonesia-eligibility filter, company-validation service, job application system, or invitation to let user-generated text direct the model.

## Explicit enablement

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-hn-story-read --enable-hn-job-discovery
```

`--enable-hn-job-discovery` fails at startup without `--enable-hn-story-read`, which itself requires local agent and reviewed agent plans. It can be disabled without changing existing HN grants or plans.

## Fixed network and authorization boundaries

- **Read**: authenticated localhost-only, origin checked, CSRF-protected `POST /api/local-hn-jobstories-preview`, with body exactly `{}`. There is no `GET` trigger, automatic startup fetch, background polling, arbitrary URL or model-invokable discovery tool.
- **First provider GET**: `https://hacker-news.firebaseio.com/v0/jobstories.json` exactly, over certificate-verified HTTPS, no credentials/tokens/cookies. The official HN API documents this job list and item-by-ID endpoint at [HackerNews/API](https://github.com/HackerNews/API). The public list is limited to **200 unique positive numeric IDs** and **8 KiB** returned bytes; incompatible/malformed lists are rejected.
- **Up to five follow-up GETs**: `https://hacker-news.firebaseio.com/v0/item/<decimal-id>.json` for the first five listed IDs **only**. Each read has an existing 6-second timeout and 16-KiB size bound; non-200 (including redirects), malformed JSON, mismatched IDs, non-job item types and missing required titles are rejected. Deleted/dead jobs and empty sanitized titles are omitted. No comment traversal, body extraction, author IDs, arbitrary redirect, user input in the provider URL or employer-site fetching.
- The response contains only **up to five bounded plain-text job titles**, HN job item IDs, original creation UTC timestamps, canonical HN links, acquisition UTC timestamp, list size, count of actual GETs and a SHA-256 fingerprint of the deterministic preview fields. Raw HTML markup and embedded `<script>` contents are discarded; the UI renders titles using text-safe DOM methods. No job body, contact information, model prompt or local plan row is included in the preview.
- **All grants remain absent** after discovery. The server never calls `HNStoryPolicy.grant` or `revoke`, cannot save a plan, cannot schedule an action and cannot dispatch `hn_public_story` on the owner's behalf. The exact existing password-confirmed grant is required to advance.
- The registry describes `hn_jobstories_preview` as `PUBLIC_READ`, **model_plannable=false**, **batch_allowed=false**, **owner_confirmation_required=true** (separate explicit user click). The server only accepts that exact registered effect and a verified opt-in; it serializes concurrent preview requests, returning HTTP 429 to extra requests.
- The existing local security audit persists `HN_JOB_DISCOVERY` ATTEMPT/SUCCESS/FAILED events **without titles, IDs, prompts or session tokens**. The actual preview is response-only and not persisted by the server, but remains visible in the signed-in browser until cleared; logout hides it. A user-approved, separately executed `hn_public_story` step still uses the original grant epoch and durable provenance in the plan ledger.

## Critical epistemic limits

Hacker News `jobstories` is **a feed of user-published job items, not an authoritative employer ATS**. It does not prove the vacancy is still open, remote, worldwide, legally open to an Indonesian applicant, suitable for the candidate's tenure, or authentic. Even a “remote” word in the title is not sufficient. The latest feed may omit many jobs. Sorting/order is provider-defined and subject to change. No paywall, account login, scraping, search filter, AI relevance scoring, personalized recommendations or automatic application is provided.

A preview can be a useful source of **candidate leads to verify later**. Do not equate discovery with eligibility. Copying a discovered ID to the grant field deliberately does **not** bypass re-entering the current owner password. Grant/revoke epoch checks are still in force.

## Evidence and QA

Synthetic Python verifies: exact static HTTPS origin/path, no auth credentials/redirect following, bounded list/shape, malformed/duplicate/Boolean IDs, unexpected item types and parents, sanitized titles/no job body, maximum 6 reads, stable fingerprint, zero mutations, explicit registry denial, HTTP auth/CSRF/Origin/exact JSON, disabled mode and audit. Real Chromium verifies user-clicked preview, source links and consent separation, selected ID into grant field, password grant and a subsequent explicitly approved HN read. Disposable Windows validates the source policy and unsafe-path protections.

The additional GitHub CI live job explicitly fetches the **current real HN jobstories feed and a few current job titles**, alongside the existing real HN fixed-item smoke. Success must be observed on the final PR head; a network failure is an unresolved gate, not grounds to claim the live source was verified.

Actual owner-PC Windows and Ollama acceptance remain deferred in [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68). Synthetic CI does not validate local model availability or correctness.

## What comes next

The next substantive gate is **independent original employer/ATS verification and Indonesia eligibility** for discovered candidates, ideally through an owner-approved set of official career/ATS domains and a deterministic source/freshness rubric. Do not treat HN text or a model summary as proof.
