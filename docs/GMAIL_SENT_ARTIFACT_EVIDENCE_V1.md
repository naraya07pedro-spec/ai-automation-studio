# Stage #120 · Independent Gmail SENT artifact witness (owner approved)

Date: 2026-11-08 WIB. Engineering-only until accepted with **real owner Windows and real Google/n8n accounts**.

## Why this exists

The Stage #116 signed final-step receipt is an assertion from the connector holder. Stage #117 checks the official n8n execution. Neither checks the downstream application. This stage adds a **separate, explicitly consented Gmail metadata-only verification** of an owner-selected SENT message.

This is deliberately narrow: even a matched Gmail record does **not** prove the message reached a recipient, that the correct customer received it, that an appointment was booked, or that this specific n8n execution **caused** this message. The Gmail message ID is owner-supplied and **not signed into the original Stage #116 receipt**. An unrelated SENT message with the same recipient and subject could match. These flags stay FALSE in storage, history, CLI output and API results.

## Strict prerequisites

- A completed, HMAC-validated Stage #116 handoff with claimed outcome `COMPLETED`.
- A Stage #117 saved result `EXECUTION_SUCCESS_CORRELATED`, `provider_finished=1` and `inbound_delivery_correlated=1`; original delivery ID/execution ID must agree with the signed receipt.
- A separately enrolled, owner-controlled Gmail Desktop OAuth credential with exactly the `gmail.metadata` scope, protected by the existing AES-256-GCM private vault. **This is not the ChatGPT Gmail connection.**
- Owner chooses the exact Gmail message ID, a single expected recipient address and exact expected subject. No recipient or subject is accepted from the AI/model/tool output.
- Operator session, CSRF, current owner password, and the typed confirmation `VERIFY_GMAIL_SENT_ARTIFACT_ONCE`.
- Enable all four required local flags; disabled by default otherwise.

## Operator-run Windows procedure

1. Follow `docs/OWNER_LIVE_N8N_ACCEPTANCE_V1.md` through the **real** provider-side n8n success-correlated acceptance. A synthetic CI record is not sufficient for live acceptance.
2. Enroll Gmail OAuth privately according to `docs/GOOGLE_GMAIL_OAUTH_METADATA_V1.md`. Do not paste Google refresh tokens, passwords, API keys or Gmail headers into ChatGPT, GitHub or shell history.
3. Start the local owner server on the same PC, using the same private master key:

```powershell
py -3 app/local_server.py --enable-encrypted-managed-webhooks --enable-n8n-execution-verify --enable-google-gmail-metadata --enable-gmail-sent-evidence
```

4. In a second Windows PowerShell session, run an **offline preflight** (only authenticated local SQLite history reads):

```powershell
py -3 tools/owner_gmail_sent_acceptance.py inspect --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID
```

5. Only after `READY_FOR_ONE_GMAIL_SENT_METADATA_READ`, explicitly authorize a single Gmail metadata read:

```powershell
py -3 tools/owner_gmail_sent_acceptance.py verify --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID
```

The CLI prompts privately for the Studio owner password, typed authorization, Gmail message ID, exact recipient and exact subject. It never prints the raw email metadata or credentials. It uses the existing cookie session and CSRF-protected localhost server, not Gmail directly. This is not a Gmail-send tool.

The backend endpoint is `POST /api/gmail-sent-evidence`, requiring exact fields `plan_id`, `gmail_message_id`, `expected_to`, `expected_subject`, `current_password`, `consent`. Read-only status is `GET /api/gmail-sent-evidence-history`. Both remain owner-session restricted. The browser UI does not yet expose a visual Stage #120 form.

## What is independently checked

- A legitimate logged-in Gmail account supplies a **single message metadata GET** for an exact path and ID from `users/me/messages/{id}?format=metadata&metadataHeaders=To&metadataHeaders=Subject`. A token refresh may precede that GET.
- The official response's ID must exactly match; SENT label must be present, TRASH absent; exactly one recipient mailbox must match the owner's expected mailbox and subject must match character-for-character.
- The inbox API and all Gmail sending/mutation endpoints remain unchanged or disallowed. The backend never requests email bodies.
- A pre-GET durable SQLite claim ensures at most one acceptance attempt per plan across restarts; aborted/uncertain attempts quarantine and cannot automatically repeat. Daily cap 5, lifetime cap 100.
- Stored evidence consists of SHA-256 fingerprints, state, timestamp and booleans only. Plain recipient, subject and message ID are not put into the evidence ledger or returned by the new APIs. Such SHA-256 hashes are **not encryption**, and low-entropy values might be guessed offline by a compromised OS owner; the encrypted Google credential is separate.
- `SENT_ARTIFACT_MATCHED` is not equal to delivery, causality or downstream business effect. `NOT_CORRELATED` does not prove that no other message was sent. `LOOKUP_UNCERTAIN` must never be retried automatically.

## Reliability and security

The n8n success gate is checked **before** any Google OAuth token refresh or Gmail read. A duplicate plan, missing HMAC claim, wrong n8n status, expired owner session, CSRF failure or missing explicit consent blocks the remote read. There is no untrusted URL, provider redirect, arbitrary mailbox crawl, model-chosen message, webhook retry, or Gmail write. Stage #120 has a separate security-audit action `GMAIL_SENT_EVIDENCE_VERIFY` and history.

The current live acceptance ceiling remains:
- `LIVE_OWNER_PC_VERIFIED=FALSE` until the actual Windows procedure passes.
- `LIVE_N8N_PROVIDER_EXECUTION_VERIFIED=FALSE` until one real official n8n API result is correlated.
- `LIVE_GMAIL_SENT_ARTIFACT_VERIFIED=FALSE` until one real Gmail message is corroborated.
- `INDEPENDENT_CAUSAL_BUSINESS_EFFECT_VERIFIED=FALSE`, even when metadata matches.
- `OFF_DEVICE_OWNER_RECOVERY_VERIFIED=FALSE` and `ONLINE_24_7_VERIFIED=FALSE`.

## Engineering verification policy

`tests/test_gmail_sent_evidence.py` covers signed/provider chain gates, metadata match/mismatch, no body, duplicate rejection, restart quarantine, daily cap, malformed path, and privacy-safe returned metadata. `.github/workflows/gmail-sent-evidence.yml` runs the focused suite on Python 3.11/3.12/3.13 with pinned encryption dependency and existing Gmail/n8n/owner-cli regressions. Full existing Python/browser CI must be checked on the final PR head before merging. CI uses synthetic Google/n8n responses only; it does not access real owner accounts.

## Next proof required

Run Stage #119 and Stage #120 against a disposable owner-controlled n8n→Gmail example and record only sanitized status codes, hashes and CI/PR SHAs. To prove **causation** later, the signed final receipt must bind the external Gmail message identity to the exact n8n execution, followed by independent provider corroboration. To prove **delivery**, separate provider-side delivered/bounce evidence is required; Gmail SENT alone is insufficient.
