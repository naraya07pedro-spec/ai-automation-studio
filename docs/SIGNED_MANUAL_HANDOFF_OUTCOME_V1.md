# Stage #116: Signed n8n manual handoff outcome receipts

Date: 2026-11-07 WIB. **Mode: local-first, no Tencent VPS, no real n8n calls and no paid AI calls in CI.**

## Operational problem solved

Stage #114 can submit **one** owner-approved HTTPS webhook from a completed plan linked to a separately recorded local workflow. The sender's immutable ledger reports `REMOTE_ACCEPTED` on HTTP 2xx or `OUTCOME_UNCERTAIN` when acknowledgement is ambiguous. **Neither means n8n actually completed the downstream workflow.** Stage #115 added a durable plan queue but deliberately did not fake external-effect verification.

Stage #116 adds a **separately authenticated, immutable, domain-separated HMAC final-step assertion** for that existing manual handoff. It reuses the encrypted managed connector's original signing secret, the Stage #114 SQLite handoff ledger and the existing per-connector sender ledger. It requires an independently recorded, finalized one-shot send attempt and binds a signed n8n result to exactly one registered connector/workflow/event/delivery ID. It **never issues a webhook POST, retries, resumes an autopilot, advances an AI plan or grants a new permission**.

The existing `app/local_n8n_delivery_proof.py` is for **scheduled managed autopilot**. This new implementation deliberately uses a different HMAC domain and proof table for **manual Stage #114 handoffs**. Cross-domain signatures are rejected.

## User flow

1. From Mission Control, complete a bounded local plan and save a deterministic local workflow event.
2. On `/handoff`, inspect and separately authorize **one** exact HTTPS send, as Stage #114 already requires.
3. In the real n8n workflow, configure an explicitly scoped final-step proof procedure. It must execute **only after the chosen final workflow step** and must separately have access to the correct secret for this connector, its configured `connector_id`, and the original inbound JSON `workflow_id`, `event_id`, `delivery_id`. The n8n execution ID must come from the real execution metadata, not be invented.
4. Have the final node create the exact HMAC-SHA256 proof below and securely hand the receipt back to the owner. Do not give the model permission to obtain or export the connector secret. A signed `COMPLETED` assertion should only be generated after the configured final n8n step actually executed; a `FAILED` assertion is also supported. These are **claims from a shared-secret signer**, not independently verified provider API results.
5. In `/handoff` section 3, keep the same selected plan/connector/workflow/event and enter the exact delivery ID, `COMPLETED` or `FAILED`, n8n execution ID, and lowercase HMAC. Re-enter the current owner password. POST `/api/signed-handoff-outcome-import` requires a valid owner session, CSRF and a fresh password. GET `/api/signed-handoff-outcomes` shows durable read-only history.
6. Importing the same exact valid receipt is idempotent and makes **zero external requests**. A conflicting signed result or a forged HMAC is refused. Receipt history remains locally readable after credential revocation; a revoked connector cannot sign new evidence through this import path.

## Exact signing contract

Domain: `AAS-MANUAL-HANDOFF-N8N-FINAL-RECEIPT-V1`

The signing message is UTF-8 / ASCII with **exactly seven fields** joined by a single LF character (`\n`), and **no trailing newline**:

```text
AAS-MANUAL-HANDOFF-N8N-FINAL-RECEIPT-V1
<connector_id>
<workflow_id>
<event_id>
<delivery_id>
<COMPLETED_or_FAILED>
<n8n_execution_id>
```

Compute **lowercase hex HMAC-SHA256** using the encrypted managed connector's original secret. The secret must be at least 32 UTF-8 bytes and at most 1024, and must not be sent with the proof or embedded in a public n8n execution log.

A reference Python signer for a *disposable synthetic example* (the production n8n final-step procedure must be separately reviewed, and no secret should be committed in source):

```python
import hashlib
import hmac
import os

domain = "AAS-MANUAL-HANDOFF-N8N-FINAL-RECEIPT-V1"
parts = [domain, "secure_manual", "outcome_flow", "outcome_event_001",
         "0123456789abcdef0123456789abcdef", "COMPLETED", "n8n-102"]
secret = os.environ["AAS_EXAMPLE_CONNECTOR_SECRET"]
print(hmac.new(secret.encode("utf-8"), "\n".join(parts).encode("ascii"),
               hashlib.sha256).hexdigest())
```

The receipt import request contains exactly these eight fields:
`plan_id`, `connector_id`, `workflow_id`, `event_id`, `delivery_id`, `outcome`, `execution_id`, `proof_hmac_sha256`, plus `current_password` *only* on the authenticated HTTP endpoint. `plan_id` is the owner-selected **local binding**, not a value claimed by the remote n8n signer; the server checks its exact immutable source association separately.

## Validation guarantees

- Connector exists, is active, and uses the **AES-256-GCM** private registry. Legacy plaintext storage is rejected. Import rejects short or missing HMAC secrets.
- Exact existing Stage #114 handoff (plan, connector, workflow, event) is already claimed; a preview-only record is not eligible.
- The matching existing per-connector send ledger must record the *same* delivery ID and a finalized attempt status (`REMOTE_ACCEPTED` or `OUTCOME_UNCERTAIN`).
- The saved original payload SHA-256 and destination URL hash must match the sender's independent ledger; mismatches fail closed.
- HMAC uses a dedicated domain, connector/workflow/event/delivery identity, claimed outcome and n8n execution ID. It is checked in constant time, rejecting changes to any signed field and replay across destinations or delivery IDs.
- The separate signed claim is stored **in the same SQLite database as Stage #114 handoffs**, with plan primary key and unique connector/delivery ID. Existing dispatch state stays unchanged; the claim is not equivalent to a new send.
- `COMPLETED` and `FAILED` are both only **provider-asserted outcomes**. HTTP responses always include `independently_verified_remote=false` and `business_effect_verified=false`.
- If a real send timed out but the remote signer later generates a valid receipt, the owner may import it to **record** that claim. It does not authorize replay, assume downstream success or change the original sender's `OUTCOME_UNCERTAIN` status.
- Duplicate identical receipts return `ALREADY_RECORDED`; conflicting signed claims are refused. No last-write-wins overwrite.

## Limits and attacks that remain in scope

HMAC proves someone had the shared secret. An authorized insider or compromised n8n instance could sign a fabricated completion without actually executing the work. **No authenticated read of the n8n API, CRM, payment provider, or Gmail downstream outcome occurs here.** Such independent provider-side verification needs scoped read-only credentials, official endpoint semantics, durable rate limiting, verified API responses and separate user consent in a later stage. Do not describe this stage as remotely independent or as production end-to-end business success.

Secret rotation/revocation intentionally prevents validating a *new* receipt using the previous secret; already recorded proof rows remain visible. The local owner device/SQLite state must be protected and backed up. CI does not establish live Windows ACLs, live webhook acceptance, actual provider execution IDs, or genuine business effects.

## Acceptance and regression

`tests/test_signed_manual_handoff_outcome.py` exercises valid signed final claim, failed assertion, forged HMAC, altered fields, no-send denial, nonexistent delivery, idempotent exact re-import, signed contradictory result denial, uncertain transport without retry, restart persistence, revoked credentials, tampered payload, unfinished local send, malformed data, and a real owner-authenticated loopback HTTP import with CSRF/password/audit.

`.github/workflows/signed-handoff-outcome.yml` installs the optional pinned AES-GCM dependency and runs the focused suite + Stage #114 manual handoff, scheduled signed n8n importer, vault and connector regressions on Python 3.11, 3.12 and 3.13. Full repository synthetic and browser CI must also pass at the exact PR head before merge.

**Unverified live acceptance:** `LIVE_N8N_MANUAL_FINAL_STEP_VERIFIED=FALSE`, `INDEPENDENT_BUSINESS_EFFECT_VERIFIED=FALSE`, `ONLINE_24_7_VERIFIED=FALSE`.
