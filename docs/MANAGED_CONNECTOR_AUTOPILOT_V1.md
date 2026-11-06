# Managed Connector Autopilot V1 · 2026-11-06

## Real milestone

PR #101 extends the **already implemented** real SQLite recurring worker, owner-managed HTTPS/n8n connections, and bounded single-destination webhook autopilot into **per-connector automatic scheduling**. It does not create an unbounded general AI agent or imply all ChatGPT plugins can be copied. Every grant is deliberately bound to a **specific encrypted managed connection ID and recurring schedule**.

**Only encrypted managed connections may use this capability**. Plaintext `--enable-managed-webhooks` is refused by the managed-autopilot entrypoint. Both the connector manager and this capability must be explicitly enabled. Registration of a connector never automatically grants scheduling.

## How to use on Windows

1. Update the reviewed Git checkout and install optional crypto: `python -m pip install -r requirements-vault.txt`. Set the **same owner-backed-up independent** `AAS_VAULT_MASTER_KEY` that encrypts the managed connector vault. Do not share the key, credentials or webhook URLs in this chat.
2. Start `.\START_AI_STUDIO.cmd --mode offline --enable-encrypted-managed-webhooks --enable-managed-autopilot` from PowerShell.
3. Sign in at `http://127.0.0.1:8765/mission`. In **04 · Local Automation**, create and activate a deterministic rule workflow. In **05 · Recurring Scheduler**, create an active cadence from every minute to monthly. In **08 · Managed Connections**, register one or more exact HTTPS/n8n destinations, each with its own ID.
4. Open **10 · Managed Connector Autopilot**. Select one active connection and one active schedule, set **1–30 maximum total POST attempts** and **1–90 days** expiry, then review the exact destination hostname, source workflow, version, cadence, eligible start and cap. Check consent and **re-enter your current Studio owner password**. No API/model can arm the grant without authentication and CSRF. Another explicit authorization is needed for a second connection, even if it uses the same schedule.
5. At future due times, the real server worker executes the deterministic local rule. If a valid active grant exists, it claims that schedule slot durably and sends the existing fixed-schema pseudonymous receipt over pinned HTTPS to the chosen connection. Browser closure does not stop the worker; stopping the Python server or VPS service does. No action is triggered immediately by previewing or registering a connection.
6. Review the per-connector policy/attempt history in section 10. Use **STOP** to disable future claims. Revoking the connection in section 08 also blocks subsequent sends, including when it was previously authorized. A revoke during an already active network call cannot recall that request; it waits for that call's completion before revocation finishes.

## Private Linux/Tencent VPS (when separately verified)

On the host with the verified private systemd setup, use the already established root-only `LoadCredential` vault key and explicitly include:

`--enable-encrypted-managed-webhooks --enable-managed-autopilot`

in the fresh service installer (or carry out a carefully reviewed operator-managed unit update for a prior service). The new managed scheduled-send mode uses the **same encrypted connector registry** as section 08; it does NOT require `--enable-webhook` or the separate fixed `AAS_WEBHOOK_URL` legacy single-destination target. Keep port 8765 bound to localhost and access through the existing SSH tunnel. Avoid exposing 8765 to the internet.

The service is not deployed to the owner's Tencent VM by this PR; any 24/7 claims require first-party cloud-state, SSH, systemd, restart and external-receipt verification.

## Identity, grants, durability and failure modes

- Each encrypted connector has an immutable, permanently reserved ID. Each connector has its own persistent `managed-scheduled-webhook-ledgers/CONNECTOR_ID.sqlite3` file and per-schedule policy/attempt uniqueness. The generated delivery ID includes **connector ID + schedule ID + scheduled slot**, preventing collision between separately authorized destinations.
- The owner review token expires after five minutes. It binds connector destination URL hash, schedule configuration/epoch, workflow version, earliest due slot, total attempts and expiry. Changes require fresh approval. No wildcard URLs or model-selected arbitrary payloads.
- All external effects are preceded by durable SQLite `IN_FLIGHT` claims. After crash/timeout/uncertain network response, the corresponding policy is paused. No blind retries. HTTP 2xx means **endpoint accepted**, not proof the downstream n8n workflow or CRM action completed. Check independent n8n delivery IDs for confirmation.
- The manager lock is held during each approved outbound call so a credential cannot be deleted/replaced mid-request. A revocation already finished before a request starts makes the request fail closed. Stopping an in-progress send is not physically possible.
- Separate dedicated audit names distinguish owner policy changes (`MANAGED_AUTOPILOT_POLICY`) and automated dispatch (`MANAGED_AUTOPILOT_DISPATCH`) from legacy fixed-destination sends. Logs, status and the interface show no master keys, webhook secret paths or signing secrets.
- No automatic sending from Gmail OAuth; no Gmail/CRM write access; no general plugin installation; no model execution on schedule. The worker still evaluates **local deterministic rules**, not an LLM-generated multi-tool plan.
- The previous **single fixed-destination** owner-armed autopilot remains independently available. Both can be enabled explicitly, but two separately authorized endpoints can intentionally receive the same source schedule receipt. This is not a unique exactly-once delivery guarantee across distinct destinations.
- The policy/attempt store currently relies on local SQLite durability and server process lifecycle; this is not a multi-node distributed queue or high-availability guarantee. Retain backups of **all** SQLite ledgers and encrypted vault together with separately backed-up master key before using important business tasks.

## Validation and external acceptance

`tests/test_managed_scheduled_webhook.py` uses a real encrypted vault + SQLite schedule receipts with an injected sender, covering first-send, multi-target isolation, hard cap, stop, revoke, race, crash recovery, uncertain outcomes and plaintext rejection. `tests/test_managed_scheduled_webhook_http.py` validates actual loopback session/CSRF and password-gated arm/stop. Dedicated CI installs PyCA `cryptography`, runs original legacy autopilot tests and verifies the Mission Control script/systemd unit.

**No owner n8n webhook has been called live, no Google account consent was requested, and no actual Tencent service or restart has been verified.** Passing CI proves bounded implementation behavior on disposable test fixtures, not external deployment success.
