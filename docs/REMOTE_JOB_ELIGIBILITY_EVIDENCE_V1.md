# Slice 81: Conservative remote-job eligibility language triage

## Delivered contract

Slice 80 already reads one explicitly owner-selected Greenhouse public Job Board posting. Slice 81 uses the SAME owner-clicked, authenticated, CSRF-protected and audited one-GET result. It adds a deterministic, no-model, no-new-network text-evidence assessment as the JSON field **eligibility_triage**. No grant, automatic task, applicant information, external write, file write, job application or polling is created.

Run the workbench with:

    python app/local_server.py --enable-local-agent --enable-agent-plans --enable-greenhouse-evidence

Enter one Greenhouse board token and an exact positive job ID. Click the existing official check. Review the full source, the eligibility_triage findings, and their short verbatim public posting snippets in the results panel.

## Strong distinctions

- Work location: the assessor treats **Indonesia** as the candidate's proposed work location, not their nationality or visa status. It does not load any individual credentials or work permits.
- Explicit other-country residence requirements such as "Applicants must be based in the United States" produce the signal **POSTING_LOCATION_CONFLICT_FOR_INDONESIA**. That is a public posting-language conflict, not a conclusive legal decision or an automated rejection.
- "US-only work authorization required" and "no visa sponsorship" are instead reported as a **foreign authorization term** requiring verification. Authorization and physical work location are not interchangeable.
- "We hire candidates based in Indonesia" can be indexed as an Indonesia-related employer statement, but the returned eligibility verdict remains **MANUAL_REVIEW_REQUIRED**.
- Bare words like "Remote", "Worldwide" and "Hybrid" are not proof of Indonesia eligibility. Mixed onsite/remote terms are flagged for review, not resolved by guessing.
- A bounded literal requirement such as "5+ years of experience" is surfaced without automatically deciding candidate seniority fit.
- Conflicting location statements produce **CONFLICTING_LOCATION_TERMS** and must be verified with the employer.

The preexisting top-level flags remain false in all cases: indonesia_eligibility_verified, remote_work_verified, accepting_applications_verified, employer_identity_independently_verified and hn_to_ats_match_verified.

## Evidence and integrity

- A static analyzer strips scripts, styles, HTML markup and control characters. It inspects up to 12,000 text characters of the bounded public posting description (from at most the first 48,000 source characters). Short evidence quotes are capped at 190 characters. If capped, description_text_truncated is true.
- Scope is a source-language signal index, NOT a definitive employment determination. Pattern-based extraction can miss paraphrases, multi-clause negation, later text beyond truncation, eligibility exceptions and specific experience requirements.
- The result includes candidate_work_location, candidate_work_authorization: NOT_ASSESSED, Indonesia-related location signal, remote_mode_signal, foreign_work_authorization_signal, seniority_signal, evidence quotations and a limitations notice.
- Existing official API job URL, retrieved source metadata, check time and SHA-256 are preserved. The SHA now covers the new deterministic triage output too. The volatile check timestamp stays outside the hashed evidence.
- The existing Greenhouse server gate validates the triage envelope and no-auto-apply invariant. The browser warns about explicit geographic wording and shows the exact source evidence. The existing security audit still stores only ATTEMPT/SUCCESS/FAILED, without source IDs, text, user profile or application details.
- The original preview still uses exactly one fixed Greenhouse HTTPS GET and zero new writes/model calls. Data stays in owner browser memory until cleared.

## Tests and remaining limitations

The new test_local_remote_job_eligibility.py suite covers geography vs work auth, explicit location exclusions, Indonesia mention without a claim of legal eligibility, conflicts, hybrid/remote ambiguity, experience requirements, HTML script suppression, truncation and unchanged connector safety. Existing server, browser, Python matrix, disposable Windows and real official Greenhouse GET jobs remain release gates on the exact final commit.

This does not verify legal authorization, accepting-applications status, candidate-specific fit, employer careers form, hourly timezone overlap, job authenticity outside the ATS, or actual possibility of a contract from Indonesia. Owner-PC ACL and local Ollama hardware verification remain deferred under Issue #68. Do not enable automatic candidate rejection or job applications from this triage.

Next opportunity: a separate employer-original country-eligibility/ATS-application-flow evidence workflow with constrained sources and explicit owner permission.
