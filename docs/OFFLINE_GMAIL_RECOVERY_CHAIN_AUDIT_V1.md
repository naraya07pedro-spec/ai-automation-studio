# Stage #124 · Strict offline Gmail evidence reconciliation after recovery

**Engineering objective, 2026-11-08 WIB:** audit the complete *locally saved* Stage #114–#122 n8n→Gmail evidence chain, including after secondary encrypted backup restoration, without disclosing Gmail/n8n secrets or upgrading CI success to live-provider proof.

## The real gap

Stage #118 already validates six distinct saved evidence sources through the owner-approved Stage #117 execution. Stage #123 restores and rechecks these same six ledgers after a two-copy encrypted backup. But stages #120–#122 separately persist Gmail SENT metadata, an HMAC-authenticated claim at import, and an official n8n Gmail Send node-output witness. A six-ledger-only audit **cannot tell** whether those additional records have survived the restore or now disagree. Stage #124 adds a strict, optional **nine-layer** owner audit and allows #123's recovered-copy rehearsal to demand it.

This is a *consistency verifier for stored evidence*, not a fresh independent Gmail/n8n provider check. The original Stage #121 HMAC was checked when imported, but its signature is NOT stored; the offline auditor recomputes the canonical signed **message digest**, NOT the HMAC itself. SHA-256 in local SQLite does not make the file tamperproof against a privileged same-user attacker.

## New checks, all fail closed

The original six layers (completed plan, workflow receipt, owner handoff, independent sender ledger, HMAC-signed outcome record, saved official n8n execution check) **must all pass first**, as before. Strict mode additionally requires:

1. **Stage #121 signed Gmail binding record:** exact matching plan, connector, delivery ID, n8n execution ID, Stage #116 original signed-message digest; syntactically valid Gmail ID/recipient/subject SHA-256 digests; recompute exact `AAS-GMAIL-ARTIFACT-BINDING-V1` domain-separated canonical message digest using the same signed fields; validate UTC timestamp.
2. **Stage #122 official n8n Gmail output record:** exact same execution and delivery, matching signed Gmail message-ID SHA-256, status `PROVIDER_SEND_NODE_ID_CORRELATED`, exact SHA-256 of the **owner-pinned Gmail Send node name**, valid UTC timestamp not earlier than signed-claim import.
3. **Stage #120 Gmail SENT metadata record:** exact Gmail ID, normalized recipient and subject digests bound at Stage #121, an n8n execution+delivery correlation fingerprint (exact Stage #120 scheme), status `SENT_ARTIFACT_MATCHED`, `gmail_sent_label_seen=1`, recorded metadata artifact digest present and valid, and UTC timestamp after the Stage #122 output check.

Missing tables, missing/duplicated rows, invalid or swapped hashes, changed node, stale/premature timestamps or any status short of success fail with exit code 2. The local Gmail metadata fingerprint cannot be recomputed from the original body/headers, since these sensitive inputs are intentionally never stored. Successful offline checking therefore means consistency between saved hashes and statuses, *not independent cryptographic attestation of Gmail send delivery*.

Strict mode **requires all four original owner-pinned n8n identifiers**, not guessed from a previously saved record: official API hostname, workflow ID, incoming webhook node name and outgoing Gmail Send node name. Stage #118 legacy audit continues behaving exactly as before unless strict mode is explicitly selected.

## Run read-only on the actual Windows PC

Only after a genuine owner-authorized disposable Stage #114–#122 workflow, stop Studio cleanly. Use a reviewed merged main checkout and replace sample pins/plan with actual values (none are secrets):

```powershell
cd D:\Projects\ai-automation-studio
git pull --ff-only
py -3 tools/owner_acceptance_audit.py --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID --expected-n8n-host YOUR_N8N_HOST --expected-n8n-workflow YOUR_N8N_WORKFLOW_ID --expected-n8n-node "Incoming Webhook" --expected-n8n-gmail-node "Send Gmail" --require-gmail-sent-chain
```

Outcome `LOCAL_GMAIL_EVIDENCE_CHAIN_CONSISTENT` with `checked_layers` length 9, `strict_saved_gmail_chain_audited=true`, `remote_reads=0` and `hmac_secret_reverified_offline=false` is an offline **local-record** conclusion. It does not contact Google/n8n, send email or grant new automation permissions. A broken/missing record produces `LOCAL_EVIDENCE_CHAIN_NOT_PROVEN`; do not create another Gmail send merely to turn a negative audit green.

## Prove your encrypted secondary archive can recover that full chain

The existing Stage #123 CLI now supports the same strict flag and the fourth pin during a **separately authorized** plaintext disposable restore from its SECONDARY encrypted archive. Use new, nonexistent output filenames in already existing parent directories. Prefer a physically separate removable storage device for the secondary archive:

```powershell
py -3 tools/owner_encrypted_recovery_drill.py --primary "D:\PrivateBackups\owner-124-primary.aasbak" --secondary "E:\OfflineRecovery\owner-124-secondary.aasbak" --restore-directory "D:\PrivateRestore\owner-124-disposable" --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID --expected-n8n-host YOUR_N8N_HOST --expected-n8n-workflow YOUR_N8N_WORKFLOW_ID --expected-n8n-node "Incoming Webhook" --expected-n8n-gmail-node "Send Gmail" --require-gmail-sent-chain
```

Type `RESTORE_TEST_PLAINTEXT` to authorize writing sensitive plaintext into the **new disposable** restore directory. The tool will authenticate both encrypted archives; restore the secondary; verify SQLite and disk bytes; then demand nine-layer consistency in the restored copy. A successful full rehearsal returns `RESTORED_NINE_LAYER_GMAIL_EVIDENCE_CONSISTENT` and nine audited layers. **No in-place live restore occurs, and the plaintext directory is not auto-deleted.**

No owner API keys, passwords, database records, raw Gmail message IDs or contact addresses should be pasted/uploaded to chat, GitHub or any issue. An external `AAS_VAULT_MASTER_KEY` still needs separate private recovery if its connector vault must be opened on another computer.

## Testing / limits

CI `.github/workflows/offline-gmail-recovery-chain.yml` tests on Python 3.11/3.12/3.13 with pinned cryptography. It reconstructs Stage #118's real synthetic six-ledger flow, adds private SQLite Stage #120–#122 records, attacks each saved identity/digest/status/timestamp, ensures legacy six-layer auditing stays unchanged, then does a real AES-GCM primary+secondary encrypted backup, a secondary disposable SQLite restoration and **nine-layer audit of the restored copy**. It also reruns #118 and #123 predecessor suites and the entire repo/browser gates at the same PR head.

**Not established by CI:** actual user PC execution, real n8n/Gmail API history, actual recipient delivery, independent business effects, independently proven external backup hardware, provider key recovery or online 24/7 availability. This feature never reads secrets and cannot independently revalidate the old HMAC at rest. The next essential acceptance is still operator-run Windows with actual authorized provider data.
