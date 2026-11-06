# Connector Hub V1: real n8n / custom HTTPS outbound webhook · 2026-11-06

## Release scope

This is the first **real external-write connector** in AI Automation Studio, not a promise that all plugins are installed. The new Mission Control panel **06 · Connector Hub** can send **one already-saved, pseudonymous local workflow decision** to a preconfigured **HTTPS webhook** such as an n8n Webhook node. It uses the existing owner session, CSRF, independent owner password confirmation, exact request preview, an expiring single-use token, a frozen local receipt, and a durable external-outcome ledger.

**A real HTTPS POST is only attempted when the owner explicitly confirms one preview.** No model, plan, scheduler, incoming webhook, or AI-generated tool can invoke the connector. A 2xx response means the endpoint accepted the POST, **not** that a downstream n8n workflow succeeded. Timeouts, non-2xx responses, process death and unknown provider effects are never silently retried.

## Configure a real n8n destination locally

First create an n8n workflow containing a Webhook node that accepts \`POST\` at an HTTPS production URL. Activate that workflow in n8n so its production webhook responds. If n8n is self-hosted, the configured host must resolve to **public global IPv4**, be reachable on port **443**, and present a verifiable TLS certificate matching the hostname. This connector **refuses localhost, local/private IPs, IP-literal URLs, custom ports, plain HTTP, and redirect following**. It is an **outbound-only** action. A privately hosted n8n accessible only on 127.0.0.1 is deliberately **not** supported by this release.

The app reads the full endpoint URL from \`AAS_WEBHOOK_URL\` **at server startup only**. The URL is not accepted as a browser input, stored in SQLite or returned from the connector catalog. Treat its path as a secret. Optionally set \`AAS_WEBHOOK_SIGNING_SECRET\`: the app adds \`X-AAS-Signature-SHA256\` (hex HMAC-SHA256 over the exact raw JSON body), allowing an n8n workflow to validate origin. If you use this, provision and verify the matching secret on the receiving side. Never publish secrets, webhook URLs, login passwords, tokens, or customer data in GitHub issues or ChatGPT.

### Windows PowerShell session example

Use a **new** PowerShell window in the repo directory. Replace the placeholder URL using your own working n8n production webhook. Do not commit or publish it.

\`\`\`powershell
$env:AAS_WEBHOOK_URL = 'https://your-active-n8n-host.example.org/webhook/YOUR_PRIVATE_WEBHOOK_PATH'
# Optional: $env:AAS_WEBHOOK_SIGNING_SECRET = 'PRIVATE_SHARED_SECRET'
.\START_AI_STUDIO.cmd --mode offline --enable-webhook
\`\`\`

The default double-click launch **does not** enable outbound writes. The \`--enable-webhook\` choice is separate from Ollama; \`--mode offline\` runs the real local workflow engine without any model. Environment variables last only for that PowerShell session unless you explicitly configure their lifetime.

### Linux VPS and systemd

For an actual VPS, set a privately controlled \`AAS_WEBHOOK_URL\` and optional secret in a root-protected or user-private \`EnvironmentFile\` used by the systemd service, and add \`--enable-outbound-webhook\` to the reviewed service \`ExecStart\` command. Retain all other required flags and systemd sandbox settings; do **not** open port 8765 to the internet. The existing private VPS installer deliberately refuses overwriting an existing unit. Make any changes as a separately reviewed administrator override. Do not put secrets in the unit file committed to Git, shell history, or publicly accessible websites.

Example environment file content, **stored outside the Git checkout** with access restricted to the service identity and root:

\`\`\`ini
AAS_WEBHOOK_URL=https://your-active-n8n-host.example.org/webhook/YOUR_PRIVATE_WEBHOOK_PATH
AAS_WEBHOOK_SIGNING_SECRET=your-private-optional-signing-secret
\`\`\`

Whether the user's Tencent instance is running, reachable via SSH, has safe capacity, or can reach n8n has **not** been verified. This release does not deploy to the cloud.

## Use the end-to-end outbound action

1. Open \`http://127.0.0.1:8765/mission\` over localhost or a previously verified SSH tunnel. Log in as owner.
2. In **04 · Local automation**, create and activate a local workflow, then **Evaluate and save event** using a pseudonymous ID, for example \`event_001\`. Do not use private customer data in this version.
3. Under **06 · Connector Hub**, select that saved workflow and enter its **already recorded** event ID.
4. Click **Preview actual outbound payload**. Inspect the JSON, destination hostname and SHA-256. This creates a 5-minute single-use preview token, not a remote action.
5. Re-enter your actual owner password and click **Confirm one HTTPS POST**. You must approve an additional confirmation dialog displaying the exact destination hostname, identity and digest.
6. Inspect **External delivery ledger**. \`REMOTE_ACCEPTED\` means a 2xx status was observed, **not** proof that n8n completed. \`OUTCOME_UNCERTAIN\` means no safe conclusion is available. Check the n8n execution logs and the delivery ID before doing anything else.

The exact payload schema is:

\`\`\`json
{
  "schema": "aas.local_workflow_receipt.v1",
  "delivery_id": "stable-sha256-derived-32-hex-character-id",
  "workflow_id": "example_workflow_id",
  "event_id": "event_001",
  "workflow_version": 1,
  "decision": "NEEDS_REVIEW_LOCAL",
  "reason": "POLICY_REQUIRES_REVIEW",
  "created_at": "UTC-timestamp-of-original-local-event"
}
\`\`\`

The real \`delivery_id\` is computed by the app from the workflow/event pair, not the literal placeholder in this example. The app includes \`X-AAS-Delivery-ID\` in the outgoing request. **Configure downstream n8n idempotency against this delivery ID** before connecting any action that sends messages, charges money, updates records or makes irreversible changes. The app itself never retries an already claimed event.

## Security and exact limitations

- HTTPS hostname is syntactically validated, all DNS results must be globally routable, and an IPv4 address is pinned for the TLS connection to mitigate DNS rebinding. Certificate hostname verification remains active. No redirects, proxies, non-443 port, arbitrary model URL or client-supplied request body.
- A saved workflow/event receipt is read from the existing \`local_runs\` SQLite table and converted to a fixed JSON schema. The browser cannot change its content. Only one write attempt is allowed per workflow/event pair, permanently, until a future separately reviewed manual reconciliation feature exists.
- \`~/.ai-automation-studio/outbound-webhook-ledger.sqlite3\` records preview/token hashes, durable in-flight claims, acknowledged/uncertain results and a bounded event history. The secret and full endpoint URL are **never** saved in this ledger. A process crash after \`IN_FLIGHT\` creates an **unresolved** claim, not a resend on restart.
- Owner password is checked again on commit, and the existing same-origin + session CSRF rules apply. The server audit stores action/status without request body, URL or password.
- The outbound runner is not a full n8n node editor, HTTP MCP client, OAuth manager or plugin marketplace. Gmail, HubSpot, WhatsApp, calendar sync and generic plugin installation are still **NOT IMPLEMENTED** in this Studio. n8n may bridge to other services under **its own separately configured permissions**. The app is not claiming those bridge capabilities itself.
- The server should run under a dedicated local non-root account. Process environment variables can be visible to that account and administrators; this is not an encrypted secrets vault. Production-grade credential rotation, backups, independent downstream receipt verification and incident recovery are still outstanding.
- The example URLs are placeholders only; do not assume they are active. No successful delivery to the user's actual n8n endpoint was verified in CI or via the current connected tools.

## Engineering validation gate

Targeted tests use an **injected** transport to verify one-use send/claim, preview expiration, destination changes, restart persistence, uncertain-no-retry, DNS private-address rejection, TLS request headers and exactly one HTTP call with no redirect. The existing disposable localhost smoke verifies configured catalog, preview, and rejection of an incorrect owner password with **no external send**. CI success demonstrates code paths, **not real n8n delivery**.
