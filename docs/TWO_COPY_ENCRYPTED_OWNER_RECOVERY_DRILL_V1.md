# Stage #123 · Owner-controlled two-copy encrypted backup and recovery drill

**Engineering stage: 2026-11-08 WIB. No real owner laptop/storage or cloud account accessed in CI.**

## Why this exists

Stage #111 already has a correct general approach: offline owner-state SQLite snapshot, AES-256-GCM encryption, manifest/digest verification and a manual create-only plaintext restore test. Stage #118 already has a six-ledger semantic consistency audit for real owner-authorized Stage #114–#117 events. Neither alone proves the owner has **two independently readable archived copies** and that a recovered copy still contains internally consistent plan, workflow, connector send, signed n8n claim and provider-check records.

Stage #123 intentionally **reuses** those existing implementations instead of constructing a second backup format. One owner-run command composes them:

1. Preflight checks that source folder exists, new primary/secondary filenames are distinct, destination paths are outside live owner state and Git checkout, and any optional plaintext restore location does not collide with either archive.
2. Studio must be cleanly stopped (original exclusive `127.0.0.1:8765` guard). Build one encrypted snapshot. Owner backup passphrase is prompted without echoing and is never supplied on a command line.
3. Open and authenticate the primary archive using `offline_owner_backup.verify()`; independently hash ciphertext using streaming chunks. Create a secondary ciphertext file **once**, exclusively, without overwrite, plaintext staging or additional compression.
4. Compare full archive SHA-256 and byte count between both files. Independently authenticate/decrypt and integrity-check the secondary archive. Refuse incomplete/modified/uncopyable copies.
5. **Optional only**, after typing `RESTORE_TEST_PLAINTEXT`, decrypt the secondary copy into a new disposable plaintext folder. Independently re-check restored hashes and SQLite integrity.
6. **Optional only**, if supplied an existing completed plan ID, run Stage #118's **real offline six-ledger semantic audit** against that disposable restored folder, with optional pinned n8n host/workflow/node. A restored database passing SQLite `quick_check` alone is not enough to call this chain consistent.

This is deliberate **disaster-recovery acceptance**, not autoscheduling and not a production restore. It never sends Gmail/webhooks, calls n8n/OpenAI, exports private records to GitHub, overwrites live state, or deletes a restored plaintext directory automatically.

## Run on the real Windows owner PC

After reviewing the merged main branch, STOP the local Studio service and make sure it is not listening at `127.0.0.1:8765`. Run as your normal Windows user. Create private, already-existing parent directories for **two different new archive filenames**, preferably using a separate removable drive for the secondary. For example, assuming `D:\PrivateBackups` and `E:\OfflineRecovery` already exist:

```powershell
cd D:\Projects\ai-automation-studio
git pull --ff-only
py -3 -m pip install -r requirements-vault.txt
py -3 tools/owner_encrypted_recovery_drill.py --primary "D:\PrivateBackups\owner-2026-11-08.aasbak" --secondary "E:\OfflineRecovery\owner-2026-11-08.aasbak"
```

The tool asks for the same **new backup passphrase twice** without echo. Neither filename may already exist. It outputs a sanitized JSON status including sealed ciphertext digest, file count, independent archive decryption status and `physical_off_device_copy_independently_verified=false`. **Do not post that passphrase, your owner databases, or the backup archive to ChatGPT/GitHub.**

For the optional disposable plaintext restore rehearsal and **actual recovered six-ledger consistency** (requires one genuine previously completed #114–#117 local plan ID), use a fresh pair of archive names and a new nonexistent disposable destination:

```powershell
py -3 tools/owner_encrypted_recovery_drill.py --primary "D:\PrivateBackups\owner-drill-002.aasbak" --secondary "E:\OfflineRecovery\owner-drill-002.aasbak" --restore-directory "D:\PrivateRestore\owner-drill-002" --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID --expected-n8n-host your-instance.app.n8n.cloud --expected-n8n-workflow 201 --expected-n8n-node "Webhook Test"
```

**Type `RESTORE_TEST_PLAINTEXT`** to authorize creating plaintext sensitive state in the new folder. The `D:\PrivateRestore` parent must already exist. The program never restores into the active `~/.ai-automation-studio`, never automatically launches Studio against the restored directory, and never silently deletes it. Review and securely protect/clean up that disposable plaintext directory yourself. An interrupted operation can leave a partial copy or plaintext folder; inspect rather than trusting an incomplete result.

For the semantic audit, the expected host/workflow/node example values must be replaced with your *actual pinned configuration*, and a valid plan must already exist. A failed semantic audit is reported as NOT PROVEN, even if decryption and SQLite checks succeeded.

## Explicit limits

- Archive authentication, equal sealed SHA-256 and two verified paths do **not prove physically independent devices** or offsite disaster recovery. The tool reports a filesystem ID difference as a hint, not a claim about physical hardware. Actual independence and removable/offsite retention require separate physical inspection.
- The existing backup format is unchanged. Existing constraints remain: 150 files, 32 MiB/file, 128 MiB total raw owner state, 134 MiB encrypted archive. Capacity errors halt the operation.
- Studio's port guard prevents normal localhost startup during snapshot but not arbitrary same-user writers, other custom-port services, OS failure or power loss. Stop all writers; do not assume transactional consistency across independent SQLite databases under concurrent write.
- The encrypted connector vault key `AAS_VAULT_MASTER_KEY`, external provider API keys, Gmail OAuth application credentials/permissions, and remote n8n data are **not thereby proven recoverable**. Store required external keys through a separate secure process. This tool never exposes them.
- Windows private file ACLs differ from Unix mode bits. Review `scripts/check_windows_local_acl.ps1` and storage ACLs; use BitLocker/Windows protected storage where appropriate. A JSON success status does not certify ACL correctness.
- Six-ledger audit measures local stored cross-ledger consistency, not whether live n8n/Gmail genuinely completed the intended external actions. A privileged attacker could have altered local history consistently.
- The operator must preserve independent copies, test them periodically, and make an explicit later choice about retention/rotation. This stage introduces no background backup, automatic disposal, production recovery or VPS requirements.

## Verification gates

The dedicated `.github/workflows/owner-encrypted-recovery-drill.yml` runs on Python 3.11/3.12/3.13 with actual pinned AES-GCM dependency, tests legitimate two-copy creation, separate authenticated decryption, exact ciphertext byte comparison, no plaintext by default, optional SQLite restore, overwritten/path collision refusal, injected secondary corruption/disk failure, wrong password, CLI privacy, and an actual six-ledger restore using the existing Stage #118 fixture. It also runs Stage #111 backup and Stage #118 offline audit regression suites. Full repo and browser CI on exact PR head must also succeed before merge.

**Live flags must remain FALSE until actual owner-run evidence:** `OWNER_WINDOWS_LIVE_VERIFIED=FALSE`, `OFF_DEVICE_OWNER_RECOVERY_VERIFIED=FALSE`, `REAL_OWNER_SIX_LEDGER_RESTORE_VERIFIED=FALSE`, `LIVE_N8N_EXECUTION_VERIFIED=FALSE`, `LIVE_GMAIL_SENT_ARTIFACT_VERIFIED=FALSE`, `ONLINE_24_7_VERIFIED=FALSE`.

**Next priority once merged:** actually execute this with reviewed owner credentials and data on Windows, then run a disposable n8n→Gmail #122 provider acceptance. More synthetic stages alone are not a substitute.
