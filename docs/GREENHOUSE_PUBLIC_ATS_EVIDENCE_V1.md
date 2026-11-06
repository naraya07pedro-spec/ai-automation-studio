# Slice 80 · Official Greenhouse public ATS posting evidence, separate from HN claims

## Product outcome

The authenticated local workbench can now independently check **one exact Greenhouse public job post** through the ATS provider's official [Job Board API](https://docs.greenhouse.io/job-board), without using a model or an arbitrary website fetcher. The owner enters a **lowercase board token** taken from the employer's official careers/Greenhouse page and a **numeric job posting ID**. A separately optional HN story ID is an explicitly **owner-supplied, unverified reference**, not proof the two items describe the same vacancy.

Start the protected local workbench with:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-greenhouse-evidence
```

Then choose **Check official ATS posting (read-only)**, review the returned evidence, its official source link, public location label, publication/update dates and a bounded human-readable description excerpt. This action is an independently clicked one-off check. It is **not** a model-planned tool, autonomous cross-post matching, scraper or application action. HN discovery (Slice 79) remains an independent opt-in source; it does not grant any Greenhouse action or prove HN-to-ATS identity.

## What the official source actually supports

Greenhouse's documentation specifies that public GETs require no API key and `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}` retrieves a published job post. A successful, correctly typed current GET is evidence that **that exact posting was available in the Greenhouse public Job Board API at the recorded check time**. It is stronger than relying on a social post, but *not* conclusive evidence that an employer accepts applications now, that every linked application form works, or that an Indonesian applicant can legally or operationally work in the role.

Returned flags are conservatively explicit:
- `public_job_post_observed: true` and `posting_accessible_at_check: true` only after 200 and an exact matching job ID.
- `accepting_applications_verified: false`, `remote_work_verified: false`, `indonesia_eligibility_verified: false`, and `employer_identity_independently_verified: false`. A location string such as "Remote" or "Worldwide" **never** changes those flags.
- If an owner supplies `hn_story_id`, it is stored *only in the immediate browser response* as `hn_story_id_supplied_by_owner`, with `hn_to_ats_match_verified: false`. The system never asserts that HN and ATS entries match, even if titles look similar.
- Failure `404/410` means the exact board + job post was **not found/published on this endpoint**, not evidence the employer has no other open positions or the candidate was rejected.

## Constrained integration and data provenance

- Exact owner-only, authenticated loopback **POST /api/local-greenhouse-job-preview** accepts `{"board_token":"greenhouse","job_id":1234567}` with only an optional integer `hn_story_id`. Exact JSON keys, same-origin, existing CSRF and startup opt-in are mandatory. No arbitrary URL, hostname, file path, POST application payload, webhook, browser credentials, CV or applicant personal data.
- `board_token` is a strict 1–64 character lowercase alphanumeric/underscore/hyphen token (no separators, dots, spaces, percent-encoding, mixed-case or `internal`), `job_id` is a positive integer up to 999999999 (never Boolean). The fixed connector only uses **CA-verified HTTPS to `boards-api.greenhouse.io`**, exact path `/v1/boards/<token>/jobs/<id>`, **one GET**, 8s timeout, 256 KiB response cap and no redirect following. Status not 200 is rejected. It does not submit any job application or include question fields.
- The adapter strips markup and control characters from returned title, company, location and first 640 characters of description plaintext. It never uses the provider-returned `absolute_url` (which can lead to an unrelated third-party host) as an authoritative link. It builds a canonical UI link from the validated Greenhouse token and job ID.
- The response contains only a bounded summary, official API provenance URL, canonical posting URL, UTC check/metadata timestamps, deterministic SHA-256 of the evidence fields excluding volatile check time, and exact zero `external_write_count`, `local_file_write_count`, `model_calls`, and `applicant_data_sent:false`. Provider text is public **untrusted content**, not AI instructions.
- The frozen registry adds `greenhouse_job_evidence` as an owner-click-only `PUBLIC_READ` capability with **model_plannable=false**, **batch_allowed=false** and **owner_confirmation_required=true**. No new tool-planning permission, unscoped external HTTP integration or generic network egress is granted.
- The local security audit records `GREENHOUSE_JOB_CHECK` ATTEMPT/SUCCESS/FAILED **without board token, job ID, title, HN story ID, applicant data or source text**. The check has no automatic polling and does not persist the complete record in task-plan SQLite; it remains visible in the signed-in browser until cleared on logout.

## Verification plan and bounds

- Python synthetic suite tests exact owner inputs, fixed host/path/TLS/GET, no applicant payload, JSON type checks, malformed IDs/locations/title, redirects, 404/410, oversized source, safe HTML stripping, source hash, optional owner-supplied HN ID **without** automatically claiming a cross-source match, session/Origin/CSRF, and audit.
- Disposable Windows runs this suite alongside the previous 61 ACL/workflow tests; Chromium runs a browser fixture showing evidence status and canonical link without leaking a fake malicious provider-returned URL.
- A separate GitHub Actions live job fetches the **current public Greenhouse company board** and then selects one actually published job ID to verify through the new official connector, so CI does not rely on a stale hardcoded posting. This check must succeed on the final PR commit before merging. It makes two public GETs in CI; **the product endpoint itself makes one**.

## Still explicitly unknown

This is not a complete internationally eligible remote-job discovery system. We do not verify:
- That a job accepts Indonesian or other nonresident workers, visa/work authorization, or offers global contract/EOR options.
- That a title or location proves remote eligibility, or that the employer remains willing to recruit at the moment of submitting an application.
- Whether the posting on HN corresponds to the Greenhouse requisition merely because an owner entered both IDs.
- An employer-specific custom careers form, CAPTCHA, ATS apply form health, applicant costs, years-of-experience requirements, work timezone or real interview probability.

A separate, narrowly approved original employer careers/ATS evidence workflow and explicit geography/seniority checks are the next useful steps. Actual owner PC Windows and Ollama hardware testing is **still deferred** per [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).
