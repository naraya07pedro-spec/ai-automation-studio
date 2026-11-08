# Stage #121 · HMAC-signed Gmail artifact identity binding

Recorded: 2026-11-08 WIB. **Code and CI are engineering evidence only**, not proof against real owner n8n/Gmail.

## Why this is a meaningful change

Stage #117 corroborates a specific n8n execution and exact incoming webhook data. Stage #120 independently checks that a particular Gmail SENT record exists with the owner's selected message ID, recipient and subject. **Without an identity binding**, an unrelated Gmail message could accidentally or deliberately match the same recipient/subject.

Stage #121 adds an **optional strict mode**: an owner-imported, domain-separated HMAC-SHA256 artifact claim from the trusted n8n final-step signer binds the same plan, connector, local workflow/event, delivery ID and n8n execution ID to SHA-256 digests of the exact Gmail message ID, normalized single recipient, and exact subject. The server checks existing Stage #116 and #117 identities, connector secret, and signature before storing the claim. Stage #120 then **must** match those three digests before even refreshing a Gmail token or issuing its one permitted Gmail API GET.

This is a stronger **evidence linkage**. It is NOT cryptographic proof that n8n truly sent the message: any party holding the HMAC signing secret could forge an assertion; the verifier still cannot prove Gmail delivery to the recipient, true n8n→Gmail causation, or a customer/CRM business outcome. Never mark those booleans true.

## Hard prerequisites

- Stage #114 encrypted managed connector, Stage #116 valid signed `COMPLETED` receipt and Stage #117 official provider `EXECUTION_SUCCESS_CORRELATED` record.
- A real configured n8n final-step signer with access to **the same strong connector signing secret** after the Gmail Send action has returned its official Gmail message ID. Do not place this key in JSON output, browser, GitHub or chat.
- Stage #120 Gmail Desktop OAuth metadata-only grant in the private vault, with owner consent. User-facing ChatGPT connectors are separate.
- An explicitly selected Gmail message ID, exact one-recipient `To` mailbox, and exact subject from the real workflow output. Raw values should remain inside private owner and n8n systems, not in the public repository.
- The real owner Windows PC and real n8n/Gmail accounts for live acceptance. GitHub Actions never uses those.

## Signed canonical message format

The HMAC input is **exactly ten LF-delimited ASCII lines, without trailing newline**. Sign the following concatenated string with the encrypted managed connector's 32+ byte signing secret:

```text
AAS-GMAIL-ARTIFACT-BINDING-V1
<plan_id:24 lowercase hex>
<connector_id>
<local_workflow_id>
<local_event_id>
<delivery_id:32 lowercase hex>
<n8n_execution_id>
<sha256 lowercase hex of UTF-8 Gmail message ID>
<sha256 lowercase hex of UTF-8 recipient email after casefold>
<sha256 lowercase hex of UTF-8 exact subject>
```

The `proof_hmac_sha256` is the lowercase hex digest of `HMAC-SHA256(secret, exact_message_bytes)`.

Required signed claim JSON has exactly these ten string fields:

```json
{
  "plan_id": "aaaaaaaaaaaaaaaaaaaaaaaa",
  "connector_id": "n8n_manual",
  "workflow_id": "receipt_flow",
  "event_id": "receipt_event_001",
  "delivery_id": "dddddddddddddddddddddddddddddddd",
  "execution_id": "12345",
  "gmail_id_sha256": "<64 lowercase hex>",
  "recipient_sha256": "<64 lowercase hex>",
  "subject_sha256": "<64 lowercase hex>",
  "proof_hmac_sha256": "<64 lowercase hex>"
}
```

The hex placeholders above are **not** valid signatures. Generate the digests and signature from the actual final-step data in the owner-controlled n8n workflow. Stage #121 does not deploy n8n nodes or generate a signature on behalf of n8n, and not every hosted n8n Code node allows arbitrary cryptography modules. The signing mechanism must be checked against the actual n8n edition; do not misrepresent a signature generated manually on the owner's PC as independent n8n provenance.

The value `recipient_sha256` is over a SINGLE bare mailbox address in casefold form, **not** the `To` display-name header. Subject is exact UTF-8, no trim; Gmail message ID is exact ASCII. No recipient/header/body is saved in the Stage #121 binding table.

## Owner Windows procedure (no VPS)

1. Use the Stage #119 owner CLI and n8n official API to verify a real disposable execution. Use Stage #120 Gmail OAuth enrollment as documented.
2. Start Studio with the old capability flags **plus the new strict flag**. In PowerShell on the owner device, from the reviewed repository checkout:

```powershell
py -3 app/local_server.py --enable-encrypted-managed-webhooks --enable-n8n-execution-verify --enable-google-gmail-metadata --enable-gmail-sent-evidence --enable-signed-gmail-artifact-binding
```

3. In a second terminal, inspect the already stored n8n success using local-only reads:

```powershell
py -3 tools/owner_signed_gmail_import.py inspect --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID
```

4. After final n8n execution has produced a signed Gmail artifact JSON, import via localhost with hidden password and hidden claim input (no provider request):

```powershell
py -3 tools/owner_signed_gmail_import.py import --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID
```

Type the confirmation `IMPORT_SIGNED_GMAIL_ARTIFACT_ONCE`, supply the signed JSON privately when prompted. The server requires the current owner password, cookie session and CSRF. The exact signed content is verified against the currently active AES-GCM managed connector secret and Stage #116/#117 records. An identical re-import returns `ALREADY_RECORDED`; a conflicting claim is rejected. If the localhost acknowledgement is lost, inspect history instead of blindly repeating.

5. Verify the independent Gmail SENT record using the existing Stage #120 CLI:

```powershell
py -3 tools/owner_gmail_sent_acceptance.py inspect --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID
py -3 tools/owner_gmail_sent_acceptance.py verify --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID
```

In strict mode, missing/different signed Gmail ID, recipient or subject blocks before Google access. A successful Stage #120 match still means **Gmail SENT artifact confirmed and bound to a shared-secret assertion**, not independent causation or actual receipt by the destination.

## APIs and state

- `POST /api/signed-gmail-artifact-import`: owner-session + CSRF + fresh password + exact `IMPORT_SIGNED_GMAIL_ARTIFACT_ONCE` consent, followed by strict fields and HMAC checks. No network provider calls.
- `GET /api/signed-gmail-artifact-bindings`: read-only sanitized hash history under owner session.
- Existing `POST /api/gmail-sent-evidence`: before token refresh, strict flag requires matching signed hashes, then prior one-shot Gmail metadata behavior unchanged.
- SQL: new `signed_gmail_artifact_bindings` table on the existing private Stage #114 handoff SQLite, with immutable plan primary key; previous Stage #114–#120 tables are not rewritten.
- All three raw identifying fields are SHA-256 hashed, not encrypted. Protect the owner OS and private SQLite ledger; hashes of low-entropy values are susceptible to offline guessing.
- No model tool may import or issue this claim; no untrusted URLs, Gmail send API, n8n write, automatic retry, secret export, or cross-provider model prompt transfer is introduced.
- New stage can be disabled without breaking historical Stage #120 evidence. **Old unsigned Stage #120 receipts are not silently upgraded into signed evidence.** Strict mode requires a fresh signed claim, and prior one-shot Gmail checks cannot be repeated merely to manufacture a new success.

## CI and acceptance boundaries

Dedicated tests use actual Stage #114–#117 encrypted managed connector, valid HMAC Stage #116 claim, SQLite and Stage #117 synthetic official provider response, then Stage #121 signature import and Stage #120 synthetic Gmail GET. Negative checks: missing signed claim, forged HMAC, delivery/execution/connector mismatch, modified n8n provider status, idempotent re-import, conflicting claim, swapped Gmail ID/recipient/subject, real localhost login/CSRF/current-password/typed consent, CLI preflight and lost acknowledgement. Run focused CI with pinned cryptography on Python 3.11/3.12/3.13, all existing major Python and Chromium workflows on exact PR head before merge.

Live acceptance flags remain `OWNER_WINDOWS_LIVE_VERIFIED=FALSE`, `LIVE_N8N_EXECUTION_VERIFIED=FALSE`, `LIVE_GMAIL_SENT_BOUND_ARTIFACT_VERIFIED=FALSE`, `INDEPENDENT_CAUSAL_BUSINESS_EFFECT_VERIFIED=FALSE`, `OFF_DEVICE_OWNER_RECOVERY_VERIFIED=FALSE`, `ONLINE_24_7_VERIFIED=FALSE`.

**High-value next operational gate:** a disposable actual owner-run n8n final-node signed claim, official n8n GET, independently checked Gmail SENT record, and an encrypted backup/restore on Windows. No paid OpenAI API or Tencent VPS is needed for this workflow.
