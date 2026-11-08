# Independent-key recovery proof for encrypted Agent/project notes

Once the optional AES-256-GCM scoped-memory mode is enabled, the existing offline `.aasbak` AES-GCM archive is **not alone sufficient** to recover encrypted note text. The archive wraps the database bytes with a backup passphrase, but each note remains separately encrypted with `AAS_SCOPED_MEMORY_KEY`. Restoring only the archive without that independent key may yield a structurally valid SQLite file with unreadable notes.

This tool proves both cryptographic layers against the actual **existing sealed backup**, without writing plaintext to the filesystem or changing live owner state.

## Private owner runbook

1. Stop the Studio for the initial backup creation. Use the existing `python tools/offline_owner_backup.py create --output <outside-repo-path>.aasbak` and keep the backup passphrase privately. Verify the output with the existing backup `verify` command.
2. Keep the separate scoped-memory key securely outside the computer. A lost key cannot be reconstructed from this archive. It is not stored inside the backup or Git repository.
3. Privately provision the exact `AAS_SCOPED_MEMORY_KEY` environment variable on Windows, or a separately protected `aas-scoped-memory-key` systemd credential on Linux. Never paste real keys or backup passphrases in chat, GitHub, logs or shell commands.
4. Run:

```powershell
python tools/verify_encrypted_scoped_backup.py --archive "D:\SecureBackups\owner-2026-11-08.aasbak"
```

5. Enter the backup passphrase via the hidden terminal prompt. The script authenticates the archive, verifies the owner verifier/manifest and all embedded SQLite files, then uses the **independently provided Agent key** to decrypt and validate every encrypted scoped note's authenticated project/Agent identity, note ID, revision, provenance and expiry. It refuses mixed plaintext/ciphertext or empty databases rather than reporting a meaningless successful key proof.

The result includes only the count of decrypted notes and validated scopes. It prints no owner text, master keys, ciphertext, restored SQLite or provider credentials. It does not contact network services or issue paid model requests.

## What success proves

- The exact archive passphrase successfully authenticates the sealed backup.
- The exact independent scoped-memory key authenticates **every stored encrypted note** inside that archive.
- The SQLite file passes internal integrity checks, and all notes pass the reader's content and metadata bounds.
- The source owner directory was not modified by this read-only recovery proof.

## What it does NOT prove

- The backup was physically copied off the owner's computer or will survive loss of that device.
- The passphrase and independent key have been separately escrowed/recoverably stored.
- A complete disposable OS/device restore, live server startup, or SaaS credential recovery.
- Semantic correctness of model outputs or restoration of any third-party API, n8n, Gmail, or Tencent VPS state.
- Confidentiality of other plaintext ledgers once the backup is unlocked, or secure erasure of old plaintext files.
- Recovery of empty/no-note archives, or of other encrypted connector stores that use **different** keys.

For stronger continuity, the independent off-device backup and a disposable restore on a **second machine with the correct owner keys** remain essential physical acceptance steps. These cannot be verified by synthetic GitHub CI.

## CI

A focused test creates a genuine encrypted scoped SQLite note fixture, packages it with the existing AES-GCM owner backup tool, verifies both separate credentials, and rejects wrong keys, wrong passphrases, empty/mixed stores and tampered authenticated provenance. It runs alongside existing offline backup and sealed two-copy recovery tests using PyCA's pinned AES-GCM implementation.
