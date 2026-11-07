# Stage #118 · Offline Owner Acceptance & Six-Ledger Integrity Gate

Recorded: 2026-11-07 WIB. **No VPS, no remote API call, no live owner Windows data, no paid AI request during this stage's CI.**

## Why this exists

Stages #114–#117 introduced a meaningful chain: completed local plan, deterministic workflow receipt, one approved HTTPS send, HMAC-signed provider final-step claim, and owner-authorized independent read of the n8n execution API. The first four links can be entirely internally consistent while a **copied, corrupted, missing or mismatched SQLite ledger** makes the resulting evidence dishonest. A stage-117 lookup recorded a week ago also does **not** prove current provider state.

Stage #118 adds a deliberately strict **offline, read-only, non-secret-exporting CLI** which recomputes original cryptographic provenance and validates **six existing local evidence sources** for one exact plan:

1. Completed plan and exact model/goal/tool steps/results in `agent-task-plans.sqlite3`.
2. Original deterministic `local_runs` receipt in `local-workflows.sqlite3`, including its separately computed SHA-256.
3. Stage #114 irreversible, owner-approved handoff in `agent-connector-handoffs.sqlite3`, including plan SHA-256, workflow SHA-256 and sender payload SHA-256.
4. The **actual independent connector sender ledger** `managed-webhook-ledgers/<connector_id>.sqlite3`, verifying the same workflow/event/delivery, payload hash, and HTTP acknowledgement consistency.
5. Stage #116 signed-claim record, checking its precise claimed delivery, workflow, event, numeric execution ID, original sender status and domain-separated signed message SHA-256.
6. Stage #117 **previously saved** provider-read record, validating n8n execution/connector/delivery IDs, signed message SHA-256, original pinned hostname/workflow/node if owner supplies them, `inbound_delivery_correlated=1`, and internally consistent final provider status.

The audit **does not decrypt or revalidate the original connector HMAC secret**, does not call n8n, does not re-check a provider API signature, and does not assert an actual CRM/email/business effect. Result `LOCAL_EVIDENCE_CHAIN_CONSISTENT` means **the locally recorded chain is internally consistent**, not that live n8n was queried during this audit. The proof may still be fabricated by a compromised owner device or a dishonest source.

## Windows owner-PC commands (once there is real evidence)

Run from a reviewed local checkout on the **actual owner PC**, not from ChatGPT's synthetic CI. Use disposable test workflow data first:

1. Complete Stage #114 owner-approved handoff, import Stage #116 n8n final-step signed claim, then perform Stage #117 one-time independent read-only n8n execution lookup with real owner authorization. **Never create new external sends just for this offline audit**.
2. Shut down the Studio server **cleanly**, then confirm `http://127.0.0.1:8765/mission` is inaccessible; the CLI deliberately acquires the Studio loopback-port guard and refuses inspection while Studio is listening.
3. Copy the real 24-lowercase-hex plan ID from the Stage #114/117 local owner workbench. Record n8n hostname, workflow ID and exact incoming Webhook node name separately, without the API key.
4. From Windows PowerShell in the checked-out repository (run as normal Windows user, not Administrator unless actually required):

```powershell
py -3 tools/owner_acceptance_audit.py --plan-id <24-hex-plan-id> --expected-n8n-host your-instance.app.n8n.cloud --expected-n8n-workflow <n8n-workflow-id> --expected-n8n-node "Incoming Webhook"
```

The command does **zero HTTP requests and zero SQLite writes**, and reads only the local owner state under `~/.ai-automation-studio` by default. It does not echo the raw plan, HMAC secret, API key, workflow payload, URL path, file contents, or any personally identifying content. It prints one JSON verdict, checking individual provenance layers without containing the original source records.

Success result: `LOCAL_EVIDENCE_CHAIN_CONSISTENT`, exit code `0`. Any broken, missing, in-flight, uncorrelated, unfinished, or mismatched source: `LOCAL_EVIDENCE_CHAIN_NOT_PROVEN`, exit code `2`, a non-sensitive reason, **no auto-fix, no replay, no re-send**. The owner can investigate locally. A successful status also includes `live_provider_queried_now=false` and `business_effect_verified=false`.

**Offline-only restrictions:** the owner directory must exist, contain the original owner verifier and valid regular SQLite files with private permissions, and have a clean SQLite shutdown with no remaining WAL/SHM/journal files. Files must not be symlinked. SQLite databases are opened only in immutable read-only mode with `PRAGMA query_only=ON` and `quick_check`, not through a mutable ORM session. The verifier does not unlock the AES-256-GCM registry or decrypt credentials. On Windows, also run the separately shipped `scripts/check_windows_local_acl.ps1` as owner and inspect its result.

## Real encrypted backup and recovery acceptance

Once Studio is stopped, use the existing security-reviewed Stage #111 tool. Use a backup destination **outside** the live owner directory and Git checkout. Never share the archive or passphrase in chat or GitHub:

```powershell
py -3 tools/offline_owner_backup.py create --output "D:\\private-backups\\owner-acceptance.aasbak"
py -3 tools/offline_owner_backup.py verify --archive "D:\\private-backups\\owner-acceptance.aasbak"
py -3 tools/offline_owner_backup.py restore-test --archive "D:\\private-backups\\owner-acceptance.aasbak" --destination "D:\\private-restore-test-001"
```

The restore-test **creates a plaintext restored directory only after an explicit owner consent phrase**, and never overwrites the active state. Delete or protect that plaintext directory separately after validation. For an offline audit of the restored state, run:

```powershell
py -3 tools/owner_acceptance_audit.py --owner-root "D:\\private-restore-test-001" --plan-id <24-hex-plan-id> --expected-n8n-host your-instance.app.n8n.cloud --expected-n8n-workflow <n8n-workflow-id> --expected-n8n-node "Incoming Webhook"
```

The important distinction is **actual recovered six-ledger integrity**, not just a green encryption test.

## Security repairs shipped with this acceptance gate

The acceptance suite discovered a real interaction between SQLite WAL snapshots and the older offline encrypted backup: a SQLite in-memory `serialize()` image could retain source WAL header bytes and fail to reopen as a standalone restored database. Stage #118 repairs this by normalizing standalone journal flags after an atomic SQLite backup and checking the resulting SQLite image before encryption. Snapshot source connections use immutable read-only mode after clean shutdown to avoid creating WAL/SHM sidecars.

The suite also found that some Stage #114-supporting database constructors inherited permissive file permissions on POSIX. A new shared private-path guard creates 0600 files in 0700 directories, refuses unsafe symlinks or owners, and narrows existing owner-owned legacy files. On Windows, OS ACL inheritance is different; verify independently with the shipped local ACL diagnostic. These fixes improve backup reliability and privacy but do not constitute a live owner-PC validation.

## Tested properties and acceptance gates

`tests/test_owner_acceptance_audit.py` creates a real but fully synthetic completed plan → deterministic local workflow → AES-256-GCM managed connector → one fake network send → signed final-step claim → mocked n8n provider-execution read. After clean shutdown it runs the **actual offline CLI auditor** and tests changing the plan goal, workflow receipt, sender delivery, payload hash, signed execution ID, provider correlation, owner-pinned host/workflow/node, missing records, symlinks and stale WAL. It also creates a **real encrypted backup, authenticates it, restores into a new plaintext directory and verifies the exact six-ledger chain again**.

The dedicated CI runs on Python 3.11, 3.12 and 3.13 with optional cryptography installed; Stage #111, #114, #116, #117 regressions and full repo/browser CI must pass on exact PR head before merging.

## Hard truth about live proof

This is a **local audit tool and operator-run acceptance gate**, not a live remote testing session. Actual n8n completion needs real user-controlled API grants; independent downstream effect proof needs separate authorized CRM/email/payments reads and real provider semantics. Even if all hashes match, a malicious same-OS-user process could tamper with SQLite consistently; SHA-256 is not a tamper-proof signed attestation.

**Unverified at publication:** `REAL_OWNER_WINDOWS_ACCEPTANCE_VERIFIED=FALSE`, `LIVE_N8N_PROVIDER_EXECUTION_VERIFIED=FALSE`, `INDEPENDENT_BUSINESS_EFFECT_VERIFIED=FALSE`, `OFF_DEVICE_OWNER_RECOVERY_VERIFIED=FALSE`, `ONLINE_24_7_VERIFIED=FALSE`.

**Next highest-value action after merge:** run the single owner-PC disposable scenario with authentic n8n account, confirm its provider execution and exact incoming delivery, then perform encrypted backup/restore and this offline audit. Do not add another code-only stage to pretend the live gate was completed.
