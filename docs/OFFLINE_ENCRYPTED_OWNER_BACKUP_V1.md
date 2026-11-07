# Stage #111 · Encrypted offline owner-state backup and disposable restore

## Scope and safety boundary

This stage works **without Tencent VPS, SSH, OpenAI API requests, an Ollama
model, or background services**. The default Studio owner-state folder is
`~/.ai-automation-studio` on the **actual owner's local Windows/Linux
computer**. This is an opt-in operator CLI. It does NOT run automatically.

The existing local workbench stores crucial state across SQLite and JSON:
owner login verifier, scheduler/history, agent plans/memory, security events,
managed-connector ledgers and encrypted vault metadata. A code deploy or CI
green check cannot recover a damaged local disk. This release creates an
**encrypted snapshot of the entire bounded owner folder** and proves it can
be decrypted and restored to a *new* directory, without modifying the
existing owner state. It does NOT migrate schemas, merge SQLite databases,
rotate keys, recover forgotten passphrases or rewrite any live state.

## Windows operator commands

Use a reviewed **merged main** checkout, and close the local Studio first
(the launch console/browser alone closing does not prove the process stopped).
Example after confirming that `D:\PrivateBackups` exists and is not part of
the Git checkout:

```powershell
cd D:\Projects\ai-automation-studio
git pull --ff-only
python -m pip install -r requirements-vault.txt
python tools/offline_owner_backup.py create --output "D:\PrivateBackups\studio-backup-2026-11-07.aasbak"
python tools/offline_owner_backup.py verify --archive "D:\PrivateBackups\studio-backup-2026-11-07.aasbak"
```

The CLI privately asks for a **16–256-character passphrase** through a hidden
terminal prompt, twice for creation. Use a unique high-entropy passphrase
stored in an **independent password manager or offline record**, not in the
same archive, GitHub, a terminal command argument, chat or a shell script.
Losing the passphrase means the encrypted archive cannot be restored.
The backup destination must be a **new filename** under an existing parent
directory outside the live owner folder and Git checkout. Existing archives
are never overwritten.

The CLI acquires an exclusive loopback bind on `127.0.0.1:8765` while
snapshotting, refusing an already-running Studio process using that
port. It rejects symbolic links and hardlinks, symlinked destinations,
unsafe POSIX permissions, journal/WAL/SHM SQLite sidecars, corrupt SQLite,
unsupported filenames, and oversized source data. After an orderly Studio
shutdown, ordinary SQLite files are copied via the SQLite backup interface
into memory and serialized. No plaintext intermediate database, ZIP archive
or temporary source directory is created on disk. AES-256-GCM authenticates
the entire zipped snapshot; keys are derived from the supplied passphrase
with scrypt and random salt. SHA-256 file digests are embedded **inside the
encrypted manifest**.

Limits for v1: maximum 150 files, 32 MiB per file, 128 MiB total original
data, 134 MiB encrypted archive. Oversized state is a blocking error, not
a partial success. These are safety limits, **not future-proof capacity
guarantees**. The backup includes files such as local reports/export
artifacts if they reside inside the owner's folder, so treat it as
sensitive even though encrypted.

## Verify and restore-test

The `verify` command decrypts the authenticated archive in memory, checks
the manifest, per-file digests and SQLite integrity, without restoring
plaintext to disk. It reports `ENCRYPTED_BACKUP_AUTHENTICATED_AND_SQLITE_CHECKED`
when successful. This verifies *archive integrity and readability*, not
that an interrupted external provider action completed.

A deliberate **restore test** creates a **new, separate plaintext folder**
only after typing the literal phrase `RESTORE_TEST_PLAINTEXT`:

```powershell
python tools/offline_owner_backup.py restore-test --archive "D:\PrivateBackups\studio-backup-2026-11-07.aasbak" --destination "D:\PrivateBackups\studio-restore-test"
```

The destination **must not exist**. The program refuses destinations inside
the owner's live state or checkout, authenticates the archive first, writes
all files exclusively, checks their disk hashes and SQLite integrity,
and never launches Studio against the restored copy. It does not overwrite,
merge or erase any existing file. The restored test folder is **plaintext
sensitive data**: inspect it in a private location, then manually remove it
after confirming success. If the copy fails partway through, the new folder
remains for local inspection. Do not share it.

This is a **disposable restore rehearsal**; it is not production restore into
`~/.ai-automation-studio`. A real disaster restore requires a separate,
manually reviewed offline recovery process on a clean machine and
carefully matched external credentials. No automatic production replacement
is implemented.

## Limitations worth keeping explicit

- File-system lock on port 8765 only coordinates the default Studio
  process. A custom-port server, malicious same-user process, concurrent
  file writer or power loss may still defeat a coherent checkpoint.
- An offline backup cannot recover an already corrupted source database.
  SQLite integrity check detects many forms of corruption, not all
  semantic inconsistency across separate files.
- On Windows, file ACL enforcement depends on the OS user profile and
  destination ACL; `chmod(0600)` is not a substitute for a Windows ACL audit.
  Use a private directory and BitLocker/encrypted disk when available.
- This archive includes the encrypted connector-vault files but **does not
  capture the independent `AAS_VAULT_MASTER_KEY`**. Save that external
  key securely and independently if encrypted connectors must be recoverable.
  Likewise, OpenAI API keys and any VPS systemd root credentials are not
  backed up from environment variables or `/etc`.
- There is no automatic offsite backup, retention schedule, passphrase escrow,
  provider reconciliation, load test, guaranteed crash-safe snapshot under
  concurrent writing, or verified recovery of live business integrations.
- For cross-device portability, verify all required external dependencies,
  API credentials, host permissions and data-location rules separately.

## CI evidence

`tests/test_offline_owner_backup.py` uses **disposable** state, encrypted
fixture archives, tamper and passphrase rejection, no-overwrite rules, WAL
fail-closed behavior, and independent SQLite-restored readback. The
dedicated GitHub workflow runs Python 3.11, 3.12 and 3.13 with pinned
`cryptography`. CI never accesses the owner's Windows directory,
Tencent VPS, provider billing or actual business credentials. Test success
is not evidence that the user's real state was backed up or restored.

`LIVE_OWNER_BACKUP_VERIFIED=FALSE` until the owner locally runs create,
verify and restore-test and inspects those actual outputs.
