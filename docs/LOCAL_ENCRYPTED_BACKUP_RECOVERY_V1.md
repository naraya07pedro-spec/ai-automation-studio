# Stage #111: Offline encrypted owner-state backup and disposable recovery

## Purpose

The Studio persists scheduler history, memory, owner login, security audit, and connector delivery ledgers as separate SQLite and JSON files under the OS account's private .ai-automation-studio directory. Data loss can erase operational history. This release adds a bounded, owner-controlled, encrypted backup and a verified disposable restore **without VPS, cloud integration, background automation or paid inference**.

## Cryptography and data consistency

The backup tool at tools/backup_local_state.py uses SQLite's native backup API for every recognized SQLite database (including committed WAL state) and validates SQLite integrity before archiving. Ordinary state files are read with before/after stat checks. A manifest of every path, byte size, SHA-256 hash and type is encrypted along with the contents.

The archive uses PyCA cryptography AES-256-GCM, a fresh 96-bit nonce, and a 128-bit random salt per backup; the separate 16–256-character passphrase derives the encryption key using hashlib.scrypt (N=16384, r=8, p=1). No passphrase is saved in the archive or accepted in command-line arguments.

**Stop the local Studio server and SSH tunnel on port 8765 before backup.** The tool rejects capture while that port is bound. This coordination does not guarantee cross-database atomicity against a different process writing the files. Only run an offline backup when no other program is using owner state.

## Windows PowerShell operator commands

First install the already-pinned dependency from the repo:

    python -m pip install -r requirements-vault.txt

Create an empty private directory such as C:\PrivateBackups outside the Studio state root. From the checked-out repository:

    python tools/backup_local_state.py backup --archive "C:\PrivateBackups\studio-2026-11-07.aasbkp"
    python tools/backup_local_state.py verify --archive "C:\PrivateBackups\studio-2026-11-07.aasbkp"
    python tools/backup_local_state.py restore-disposable --archive "C:\PrivateBackups\studio-2026-11-07.aasbkp" --destination "C:\PrivateBackups\restore-check-2026-11-07"

Each command prompts privately for the backup passphrase; backup requests confirmation. Never place the passphrase, backup bytes, owner recovery code, or raw state into GitHub issues, chat, logs or cloud file shares. Store the passphrase securely and separately from the archive.

The backup destination must not exist yet, and no archive is overwritten. Verification does not write a restored state. Disposable recovery decrypts and verifies the entire archive first, then creates files inside a new nonexistent directory using exclusive creation. It will never overwrite the real owner data. On restore failure, partial disposable files are preserved for explicit inspection rather than silently erased.

## Coverage and exclusions

- Included: regular files and SQLite snapshots under the private state root, including owner verifier, scheduled jobs, model checkpoint, memory, security audit, exports, and managed-connector ledger files, if they meet strict permissions, path and size constraints.
- Omitted intentionally: raw SQLite -wal, -shm and -journal sidecar files. The SQLite snapshot captures committed WAL state instead.
- Not backed up: the source repository, other OS directories, systemd secrets, Windows environment secrets, external vault master keys, OpenAI keys, SSH keys, remote Gmail/n8n data, external documents, or Ollama weights.
- Limits: 1024 files, 32 MiB per file, 64 MiB total uncompressed state, 70 MiB maximum archive. Exceeding the cap fails; no silent truncation.
- POSIX verifies restrictive ownership, permissions, and hardlink count. Windows NTFS ACL isolation must be checked separately with scripts/check_windows_local_acl.ps1; os.chmod alone is not comprehensive Windows protection.

**The existing encrypted managed webhook and Gmail vaults still require the original external AAS_VAULT_MASTER_KEY.** Restored ciphertext cannot be unlocked if this key is lost. Any OpenAI API credential must also be recovered independently. A backup alone does not recreate external account access.

## Strict evidence boundary

The maximum verify status is ENCRYPTED_BACKUP_INTEGRITY_VERIFIED; the maximum restore status is DISPOSABLE_RESTORE_VERIFIED. Neither constitutes a live active-state restore, production rollout, independent offsite backup, external account recovery, provider delivery proof, Tencent VPS availability, or 24-hour unattended operation.

The GitHub Actions suite on Python 3.11, 3.12 and 3.13 tests disposable state and a real temporary SQLite database, wrong keys, corruption, traversal, symlinks, overwrite refusal, and output redaction. It never reads the user's actual laptop storage. Real recoverability requires executing the commands against actual local state, safely storing a separate encrypted copy, and checking the disposable output.
