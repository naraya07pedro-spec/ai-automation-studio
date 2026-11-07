# Stage #119 · Owner-Run Real n8n Acceptance CLI (One Verified Read, No Automatic Retry)

Recorded: 2026-11-07 WIB. **This stage makes an actual operator-run provider execution check accessible, but GitHub CI DOES NOT contact the owner's n8n service, Windows PC, or Tencent VPS.**

## What changed and why

Stages #114–#118 produced the necessary local evidence chain and a read-only provider lookup, but completing a real acceptance test still required the owner to manipulate several browser forms and manually track uncertainty. This was a practical adoption blocker. The new `tools/owner_live_n8n_acceptance.py` provides a bounded, scriptable interface to the **already implemented, privately authenticated Studio HTTP API**:

- **inspect:** log in to the existing **127.0.0.1** operator server, verify the n8n API lookup capability is enabled, match the exact **expected host, n8n workflow, Webhook node, signed receipt and claimed outcome**, verify the signed n8n execution ID is a positive integer, and ensure the local provider history has no previous check. It then logs out. **No n8n call occurs in inspect mode.**
- **verify:** repeat that preflight, require the owner to type `VERIFY_ONE_REMOTE_N8N_GET` and re-use the hidden current Studio owner password for the server's fresh-password gate. It issues **at most one localhost POST** to `/api/n8n-execution-check`. That endpoint alone may make **one official n8n GET**. It never submits a webhook, runs a model, changes n8n workflow state, sends an email, or retries an uncertain request.
- The tool reads only fixed localhost paths, disables proxy use, rejects redirects and non-JSON responses, enforces 64 KiB local response size limit, and does not print or persist raw API response, API key, owner password, full plan, webhook URL, execution ID or delivery ID.
- Authentication uses the actual operator session cookie and CSRF token. The session is explicitly logged out and the cookie jar cleared on completion or error. A remote operation potentially already executed if the localhost response is lost, so it returns `LOCAL_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY` without sending another POST.
- `N8N_EXECUTION_SUCCESS_CORRELATED` means the **existing Stage #117 verifier**, not the CLI itself, confirmed exact saved execution + incoming delivery against the official n8n API. It **does not prove a CRM/email/payment/customer business outcome**. `N8N_EXECUTION_FAILED_CORRELATED` verifies a reported failed execution, so it must not be interpreted as a successful job.

## Exact Windows owner procedure

Prerequisites: Python and installed dependencies from the repository; actual owner credentials already provisioned; actual encrypted Stage #114 connector; previously approved completed plan, actual one-shot handoff, actual Stage #116 final-step **signed** receipt with a **numeric n8n execution ID**. The n8n public API must be available to the owner's account and the original incoming Webhook node data must be retained. **No model subscription, VPS or Ollama is required for this verification-only mode.**

1. On the owner PC open a reviewed, up-to-date checkout of the repository. Make sure the local owner state under `~/.ai-automation-studio` is privately permissioned. Do not upload its files, keys, or screenshots containing secret values to ChatGPT/GitHub.
2. Configure these variables in the **process environment only**, not in GitHub files or shell history: `AAS_VAULT_MASTER_KEY`, `AAS_N8N_API_KEY`, `AAS_N8N_API_ORIGIN`, `AAS_N8N_WORKFLOW_ID`, `AAS_N8N_WEBHOOK_NODE_NAME`. Prefer a narrowly scoped `execution:read` key where the n8n edition supports it. The n8n host must be public HTTPS and owner-controlled. See `docs/N8N_READONLY_EXECUTION_VERIFY_V1.md`.
3. Start Studio on the same computer with the existing encrypted connector and the n8n **verification-only** flag:

```powershell
py -3 app/local_server.py --enable-encrypted-managed-webhooks --enable-n8n-execution-verify
```

4. In **another PowerShell window**, replace only the non-secret identifiers with the exact values shown by the Studio `/handoff` UI. Before sending any remote request, inspect the local state:

```powershell
py -3 tools/owner_live_n8n_acceptance.py inspect --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID --expected-host your-instance.app.n8n.cloud --expected-workflow YOUR_N8N_WORKFLOW_ID --expected-node "Incoming Webhook" --expected-claim COMPLETED
```

Only `READY_FOR_ONE_OWNER_APPROVED_N8N_READ` (exit 0) means the saved signed receipt is sufficiently consistent to **consider** making one provider API read. It is **not** proof of remote execution. If inspect fails, do not bypass the checks or attempt a new webhook send.

5. With the host/workflow/node already confirmed, explicitly authorize **one real n8n API read**:

```powershell
py -3 tools/owner_live_n8n_acceptance.py verify --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID --expected-host your-instance.app.n8n.cloud --expected-workflow YOUR_N8N_WORKFLOW_ID --expected-node "Incoming Webhook" --expected-claim COMPLETED
```

The password prompt is hidden. The operator must separately type `VERIFY_ONE_REMOTE_N8N_GET`. A completed provider execution with a correct incoming delivery produces `N8N_EXECUTION_SUCCESS_CORRELATED`. An API failure, identity mismatch, incomplete node data, provider status conflict or ambiguous localhost connection **cannot be treated as success** and is never automatically retried. **A second verify against the same plan is intentionally blocked.**

6. If the tool reports uncertainty, review the existing `/handoff` read-only history first. **Do not rerun verify on the same plan, do not repeat the webhook, and do not invent a new execution ID to bypass the one-shot gate.**
7. Cleanly stop the local Studio server. Then run the independent **offline** six-ledger integrity check (Stage #118) and encrypt/rehearse a disposable restore using `docs/OWNER_LIVE_ACCEPTANCE_GATE_V1.md`.

```powershell
py -3 tools/owner_acceptance_audit.py --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID --expected-n8n-host your-instance.app.n8n.cloud --expected-n8n-workflow YOUR_N8N_WORKFLOW_ID --expected-n8n-node "Incoming Webhook"
```

The online Stage #119 evidence and offline Stage #118 audit provide two different independent checks: the former reads the provider's saved execution through an official API, and the latter re-computes locally recorded cross-ledger digests without network. Neither independently confirms final **downstream business effect**.

## Acceptance semantics and honesty contract

| Status | What it means |
| --- | --- |
| `READY_FOR_ONE_OWNER_APPROVED_N8N_READ` | Verified local preflight only, **zero n8n reads** |
| `N8N_EXECUTION_SUCCESS_CORRELATED` | Provider execution success and original webhook delivery corroborated through Stage #117. **Business effect unverified** |
| `N8N_EXECUTION_FAILED_CORRELATED` | A **failed** n8n execution and delivery correlated; not a successful job |
| `N8N_EXECUTION_NOT_CORRELATED_OR_CONFLICTING` | Provider data missing, error, incomplete or inconsistent with Stage #116, not a successful verification |
| `LOCAL_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY` | The owner localhost POST may have reached the server and triggered the external GET. **Never resend automatically** |
| `PREFLIGHT_BLOCKED` | Any missing signed proof, mismatched pin, disabled verifier, previous check, invalid owner password or local connection issue. No n8n GET |

Successful **local CLI execution in GitHub Actions** shows only that this interaction works with **synthetic provider responses**. The live acceptance gate can be marked TRUE only with an owner-run real n8n instance, correct signed receipt and provider-side execution record, examined independently by its owner.

## Test evidence and remaining trust boundaries

`tests/test_owner_live_n8n_acceptance.py` uses actual loopback HTTP + real password+cookie+CSRF, temporary SQLite, AES-256-GCM test registry and signed delivery, with a fake n8n network response. It covers inspect with no remote read, successful one-shot final correlation, wrong expected target, bad claimed outcome, missing consent, duplicate POST prevention, provider status contradiction, n8n timeout, lost localhost acknowledgement **after** the external read, arbitrary URL ban, redirect refusal, session revocation and redacted JSON output. The dedicated CI runs on Python 3.11/3.12/3.13 with the encryption dependency and existing Stage #116–#118 regressions. Full repo and Chromium CI are required on the exact final PR commit before merge.

**LIVE_OWNER_PC_VERIFIED=FALSE**, **LIVE_N8N_PROVIDER_EXECUTION_VERIFIED=FALSE**, **BUSINESS_EFFECT_VERIFIED=FALSE**, **OFF_DEVICE_OWNER_RECOVERY_VERIFIED=FALSE**, **ONLINE_24_7_VERIFIED=FALSE** until independently checked against the real environment.
