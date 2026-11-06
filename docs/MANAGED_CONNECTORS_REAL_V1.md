# Managed Connections V1 | 2026-11-06

## What this ships

AI Automation Studio Mission Control section 08 supports up to 4 separately registered HTTPS/n8n webhook destinations, with up to 12 lifetime IDs including revoked entries. Each connector has its own owner-selected ID, label, endpoint, optional HMAC-SHA256 signing secret, and its own durable single-use SQLite delivery ledger. One pre-recorded pseudonymous local workflow event may be sent to more than one destination after **separate owner-confirmed previews**. No AI agent, scheduler, or model automatically acquires this capability.

The registration and revocation controls are real backend operations; they do not require changing source code or restarting the server. Any actual external sending is manual, owner reauthenticated, HTTPS only and has no blind automatic retry.

## Startup

From PowerShell in the updated Git repository:

    git pull
    .\START_AI_STUDIO.cmd --mode offline --enable-managed-webhooks

The explicit startup flag is required. You do NOT need the legacy AAS_WEBHOOK_URL for this manager. The older --enable-webhook feature and the existing scheduled autopilot remain independent and do not automatically gain access to newly registered managed connectors.

Open http://127.0.0.1:8765/mission and log in as the owner.

## Use

1. In section 04, create and activate a deterministic local workflow and run a pseudonymous event, such as event_001, so it is really saved.
2. In section 08, register a unique connector ID (example: personal_n8n), a label, your own n8n HTTPS production webhook URL, and optionally a signing secret. Enter the current owner password to register.
3. Select a registered destination, workflow, and previously saved event ID. Click Preview exact JSON before sending. Check the destination hostname and fixed payload digest. No network request is made at preview.
4. Re-enter owner password and approve a separate confirmation dialogue to make exactly one HTTPS POST attempt to that destination. A 2xx response proves endpoint acceptance only, not that n8n finished.
5. Use destination-specific delivery history and independently inspect n8n execution logs, matching the stable delivery ID. Do not blindly retry uncertain outcomes.
6. Revoke a connection with owner password to erase its URL/secret from the active registry and prevent future sends. IDs remain reserved and past delivery ledgers remain available. An already in-flight request cannot be recalled.

## Storage and security limitations

**This is NOT an encrypted vault.** The registry keeps full webhook URL paths and optional signing secrets as plaintext JSON on the owner/server filesystem at ~/.ai-automation-studio/managed-webhooks.json. The application uses atomic writes and restricted POSIX permissions, directory mode 0700 and file mode 0600, and rejects an already existing POSIX registry readable by group/other. On Windows, inherited NTFS ACLs are NOT independently verified by this feature. Check NTFS permissions before using high-value production secrets. Root/admin, the service account, backup software, or malware with corresponding file access can inspect them.

The HTTP API and UI never return full webhook URL, private path or signing secret. List/history only expose a public destination hostname, user label and ID, status and delivery metadata. Register, revoke, and actual send require an active owner session, CSRF token and freshly entered current owner password. Preview also requires session and CSRF. The service audits actions without logging request bodies. No model-selected URL or free-form arbitrary request is accepted.

The private registry supports four active connectors and 12 lifetime IDs. Each URL is maximum 420 ASCII characters and must be a public HTTPS hostname on port 443, with non-root path. Existing transport performs DNS-global-only validation, pins public IPv4, verifies TLS hostname, and does not follow redirects. Each connector's no-retry ledger remains available after its secret is revoked. One send per connector+workflow+event is permitted, with uncertain outcomes treated as not safely retryable. The server is still a single-owner localhost application with no multi-server HA or OAuth.

Credential rotation with a new URL/secret requires registering a NEW ID, then revoking the old. An encrypted vault with operating-system KMS/DPAPI, provider OAuth, official Gmail/WhatsApp/CRM APIs, MCP catalog, and automatic scheduled sends via managed connections are NOT implemented. The original scheduled webhook autopilot remains bound to the independently configured legacy destination.

## Evidence and deployment

Targeted tests use injected sender callbacks; no real customer n8n network is contacted. The real disposable localhost smoke tests registration, secret redaction, preview, wrong-password refusal and revoke. A passing CI run does not prove a real n8n remote workflow succeeded or that Tencent VPS is deployed. Tencent live activation remains blocked pending actual instance access.

Related docs: OUTBOUND_CONNECTOR_REAL_V1.md, SCHEDULED_WEBHOOK_AUTOPILOT_V1.md, PROGRESS.md.
