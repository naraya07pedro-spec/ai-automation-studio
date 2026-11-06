# Signed n8n delivery proof / reconciliation V1 · 2026-11-06

## What actually changes

An HTTP 2xx only means the HTTPS endpoint accepted the request. n8n Webhook can respond Immediately or When Last Node Finishes; therefore 2xx by itself cannot prove a subsequent CRM/action completed. Official n8n docs: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook/

This release supports an **owner-imported final-step HMAC SHA-256 assertion**, separately and durably recorded against an already claimed managed connector scheduled delivery. It is NOT public callback, auto-polling, automatic resume/retry, proof of actual business settlement, or a verified real n8n account connection.

## Setup and HMAC protocol

1. Start Studio with flags --enable-encrypted-managed-webhooks and --enable-managed-autopilot and the existing, independently secured AES-GCM vault master key.
2. Section 08: register an encrypted active n8n connection with a **unique strong secret of at least 32 ASCII characters**. Existing connectors lacking a sufficiently strong signing secret cannot use this feature. Register a new connector ID if necessary. Keep secrets out of ChatGPT, logs and repository.
3. Section 10: the owner must approve a bounded schedule for that connector, and the actual sender must have created a permanent local delivery attempt first.
4. In the n8n workflow, **after the final intended downstream business action has succeeded**, configure the built-in Crypto node: Action = Hmac, Type = SHA256, Encoding = HEX; Hmac Secret credential = the same strong connector signing secret. Use a data property such as aas_proof_hmac. Official node: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.crypto

The HMAC input is **six ASCII lines separated by LF with NO trailing newline**:

    AAS-N8N-FINAL-RECEIPT-V1
    connector_id
    schedule_id
    delivery_id
    COMPLETED
    n8n_execution_id

The last five lines here mean the actual values, not the literal labels. The fifth line MUST be COMPLETED or FAILED. For n8n's final success branch the Crypto Hmac Value expression can be:

    {{ [
      'AAS-N8N-FINAL-RECEIPT-V1',
      $('Webhook').first().json.body.connector_id,
      $('Webhook').first().json.body.schedule_id,
      $('Webhook').first().json.body.delivery_id,
      'COMPLETED',
      String($execution.id)
    ].join('\n') }}

Preserve or explicitly reference the original Webhook payload if later nodes transform it. Validate the expression in your n8n version/editor. For a deliberate final failure branch, sign FAILED instead. **Do not put the success signer before the actual downstream action**. The sender HMAC or a 2xx response is NOT a final completion proof. n8n Crypto supports HMAC SHA256 and hex using a credential secret; a Code node with unrestricted Node.js modules is not needed.

## Import using Mission Control section 11

Log into the PRIVATE Mission Control (localhost or SSH tunnel) and open 11 · n8n Delivery Proof. Select the active connector and enter schedule ID, delivery ID from section 10 attempts, COMPLETED or FAILED, the actual n8n execution ID, the Crypto node HMAC output (64 lowercase hexadecimal characters), and current Studio owner password.

The server independently enforces session, CSRF, fresh owner password, validates the fixed typed envelope, checks the HMAC using the encrypted active connector secret with constant-time comparison, locates the matching (connector, schedule, delivery ID) among **actual persisted outbound attempts**, requires a finalized dispatch status, then atomically records a unique signed assertion in the existing per-connector SQLite ledger.

- RECORDED: new signed assertion matched an existing local attempt. The result is *provider-asserted*, not independently verified.
- ALREADY_VERIFIED: exact idempotent duplicate, no new record.
- n8n_proof_signature_invalid, local_delivery_attempt_not_found, conflicting_delivery_proof, strong_connector_hmac_secret_required: refused without inventing completion.

No HMAC value or secret is returned in status, audit or durable proof history. Historical verified assertions remain visible after a connector's revocation; the erased secret can no longer authorize NEW receipts. A recorded receipt does not alter REMOTE_ACCEPTED / OUTCOME_UNCERTAIN, does not re-arm PAUSED_UNCERTAIN, and never triggers another network attempt.

## Security limitations and real acceptance

HMAC is symmetric: anyone with the secret could forge an assertion. The receipt alone CANNOT prove the n8n execution actually exists or that every downstream CRM/WhatsApp action succeeded. Check the real n8n execution ledger and remote provider success separately before treating high-value outcomes as verified. Never use the AES-GCM vault master key as the connector signing secret. Keep all SQLite ledgers backed up alongside the encrypted vault and protect its separately held master key.

This stage has NO actual owner n8n, Tencent, Google or CRM credentials and performs NO live provider interaction. New tests use injected HTTPS sender and real disposable localhost HTTP sessions. Owner-side end-to-end acceptance requires a real previously authorized delivery, actual n8n Executions read-back, a FINAL-step Crypto signature, and the matching imported Studio evidence.

Implementation: app/local_n8n_delivery_proof.py; app/local_server.py; app/local_security_audit.py; web/mission.html; tests/test_n8n_delivery_proof.py; .github/workflows/n8n-signed-delivery-proof.yml.
