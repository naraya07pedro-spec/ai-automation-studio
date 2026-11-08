# Stage #122 · Official n8n Gmail Send node output witness

2026-11-08 WIB. **Engineering implementation only until the real owner-PC/n8n/Gmail acceptance is performed.**

## Why it is a real improvement

Stage #121 validates a signed assertion from the connector-secret holder. That does not prove n8n executed the Gmail send node or that the referenced message ID is in its real saved run. Stage #122 adds **one separately approved official n8n execution GET** against the exact already-correlated Stage #117 execution ID and host. It inspects ONE specifically pinned Gmail Send node output and compares the returned message ID fingerprint to the earlier signed Stage #121 artifact identity. This does not rely on arbitrary owner-selected IDs alone. The existing Stage #120 separately confirms the Gmail message's SENT metadata in the owner-controlled Gmail account.

The official n8n Gmail Message operations include Send; n8n saved executions can include `workflowData.nodes` and `data.resultData.runData`. References:
- https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/message-operations/
- https://docs.n8n.io/api/api-reference/#tag/Execution

**Critical limits:** n8n may omit execution data due to its workflow/account retention or redaction settings. The code fails closed if `workflowData.nodes` or the exact output is missing. Some node versions may return a different result shape. Validate with an owner-controlled disposable run before treating this integration as compatible. A matching node output in a saved n8n execution is evidence of a provider-reported Gmail Send result, not proof that recipient delivery occurred or business success happened. A malicious privileged n8n administrator could manipulate their own server. The evidence is independent of the Stage #121 shared-secret *assertion*, but still comes from the same n8n instance.

## Prerequisites, permissions and setup

1. Completed and verified Stage #116 signed handoff, Stage #117 official correlated successful execution, and Stage #121 immutable signed Gmail artifact identity. Do not run Stage #120 Gmail verification before #122.
2. An owner-configured n8n workflow that genuinely contains a Gmail node with `type=n8n-nodes-base.gmail`, `parameters.resource=message`, `parameters.operation=send`, and output `json.id` as a single nonempty Gmail message ID. The pinned Gmail node is distinct from the webhook input node.
3. Locally provision the existing `AAS_N8N_API_ORIGIN`, `AAS_N8N_WORKFLOW_ID`, `AAS_N8N_WEBHOOK_NODE_NAME`, `AAS_N8N_API_KEY`, encrypted connector `AAS_VAULT_MASTER_KEY`, and new `AAS_N8N_GMAIL_SEND_NODE_NAME` environment/process secret (the exact node name, for example `Send Gmail`). Use private OS environment/credential mechanism and do **not** paste secrets into GitHub, ChatGPT or shell history.
4. Separately enrolled owner-controlled Gmail Desktop OAuth `gmail.metadata` grant. No ChatGPT Gmail connection or Gmail send permission is required by the Studio verifier.

Start Studio on the reviewed owner Windows checkout using all opt-ins:

```powershell
py -3 app/local_server.py --enable-encrypted-managed-webhooks --enable-n8n-execution-verify --enable-google-gmail-metadata --enable-gmail-sent-evidence --enable-signed-gmail-artifact-binding --enable-official-n8n-gmail-output
```

Run only after the real #119 official n8n success record and the #121 signed Gmail binding are already imported:

```powershell
py -3 tools/owner_n8n_gmail_output_acceptance.py inspect --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID
```

`inspect` uses authenticated localhost history and capabilities, **zero new n8n/Gmail provider reads**.

Then deliberately authorize ONE extra official n8n GET:

```powershell
py -3 tools/owner_n8n_gmail_output_acceptance.py verify --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID
```

Type exactly `VERIFY_ONE_N8N_GMAIL_SEND_NODE_GET` and provide your current Studio owner password through hidden prompt. The owner client never takes a URL, node name or API key. It POSTs only to `127.0.0.1` with the owner session and CSRF. n8n network calls are made by the local private Studio with pinned HTTPS/DNS, no proxy and no redirect. No webhook dispatch, Gmail write, model calls or automatic retry occur.

After status `PROVIDER_SEND_NODE_ID_CORRELATED`, run the Stage #120 owner Gmail CLI `inspect`, then `verify` with separate consent. In Stage #122 strict mode, Stage #120 checks both Stage #121 signed identity and saved Stage #122 provider output before any Gmail token refresh or metadata GET.

## Strict provider matching contract

The verifier checks all of these before declaring the n8n output correlated:
- Stage #116 local workflow/event/delivery, connector, execution and HMAC digest continue agreeing with Stage #117's saved official result and the #121 immutable signed identity.
- The second response is from the same pinned API host with the same execution ID and provider workflow ID; status must be `success` and `finished=true`.
- The original inbound webhook node output **again** contains the precise original event/workflow/delivery values.
- A single snapshot `workflowData.nodes` entry must have the exact pinned Gmail node name, Gmail node type and `message/send` operation. A Code node renamed `Send Gmail` must not pass.
- Exactly one `data.resultData.runData[PINNED_NODE]` execution, one main branch, one item with a valid `json.id`; no arbitrary/fuzzy recursive searches or fallback to another node.
- SHA-256 of that exact ID must equal the signed Stage #121 ID hash. Raw n8n output and Gmail message ID never persist in the new local ledger or appear in API/CLI results.

Ledger: `n8n_gmail_output_witnesses` in the existing owner-private handoff SQLite. One pre-dispatch durable `IN_FLIGHT` claim per plan; any restart converts it to `LOOKUP_UNCERTAIN`. No same-plan retries. 5 attempts/day and 100 total. Saved statuses: `PROVIDER_SEND_NODE_ID_CORRELATED`, `PROVIDER_SEND_NODE_NOT_CORRELATED`, `LOOKUP_UNCERTAIN`. A response mismatch is not automatically retried. Corrupted/missing data never upgrades to success.

Server endpoints (both owner-session restricted):
- `GET /api/n8n-gmail-output-witness-history`: sanitized 30-record history.
- `POST /api/n8n-gmail-output-witness`: requires exact `plan_id`, `current_password`, `consent=VERIFY_ONE_N8N_GMAIL_SEND_NODE_GET`, CSRF and authenticated owner session.

Feature is **off by default**. It requires all of #117's, #120's and #121's enablement; the additional `--enable-official-n8n-gmail-output` flag cannot be used standalone.

## Testing and remaining operational gates

Dedicated Python 3.11/3.12/3.13 workflow `.github/workflows/n8n-gmail-output-witness.yml` installs pinned AES-GCM and tests synthetic official provider output, real local handoff SQLite, actual HMAC verification, correct Gmail metadata correlation, missing/malicious/multi-item node data, mislabeled Code node, mismatched incoming webhook, invalid IDs, provider errors, restart replay quarantine, already-checked legacy Gmail, owner HTTP/CSRF/password and explicit consent, CLI no-retry behavior. Existing #117/#120/#121 tests and full repo/Chromium CI must also pass on exact PR head before merging.

**NOT verified by synthetic CI:** real Windows owner environment, actual n8n Send output format/retention, real Google Gmail message, provider costs, independent recipient delivery, independently verified causal business impact, encrypted off-device restore, 24/7 availability. No Tencent VPS or paid OpenAI API is used.

**Next priority after engineering merge:** run a single disposable, owner-authorized n8n→Gmail test on the actual Windows PC and reconcile official n8n and Gmail metadata with a sanitized completion receipt. Then run encrypted backup and a disposable restore. Avoid piling on more CI-only stages instead of accepting the real operating environment.
