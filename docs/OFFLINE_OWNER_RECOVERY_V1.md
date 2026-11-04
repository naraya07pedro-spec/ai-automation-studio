# Offline One-Time Owner Recovery · Slice 45

Date: 2026-11-04. This is **offline recovery of the single local operator**, not a password reset service, cloud login, identity-provider integration or public SaaS capability.

## Why this exists

The authenticated localhost workbench (Slice 43) requires a provisioned operator. Slice 44 supports password rotation when the old password is known, and revoke/regrant controls for public GitHub read-only sources. If the password is lost, neither a browser login nor a normal rotation can help. This slice adds an **optional high-entropy recovery credential**, provisioned while the owner still knows the current password and kept **outside the computer**.

The recovery code is not generated silently on install, not stored as plaintext in owner files, not returned from HTTP, and not available to unknown users. Only a human with the previously saved one-time code, local OS account execution access, and ability to stop the localhost server can recover.

## Exact operator commands

Run all steps from the project repository on the computer that owns `~/.ai-automation-studio/operator.json`. **Stop the local server first**. CLI recovery and enrollment deliberately attempt to bind `127.0.0.1:8765` and refuse operation if the existing server is still listening.

To enroll, or rotate an existing recovery code while you still know the password:

```bash
python app/local_server.py --enable-recovery
```

The terminal prompts invisibly for the **current operator password**. Only after successful reauthentication does it print a **43-character URL-safe, 256-bit random recovery code exactly once**. Copy it into a secure offline password manager or physically protected backup stored separately from the computer. Re-enrollment immediately invalidates the previous code.

If the normal password is lost but the offline code is available:

```bash
python app/local_server.py --recover-owner
```

The CLI prompts invisibly for the **one-time code**, **new 12–128-character password**, and its confirmation. On success, it atomically replaces the local password verifier, **consumes the recovery code permanently**, revokes all process-local sessions, and prints no password or code. Then restart `python app/local_server.py` and log in using the new password. Re-enable a new offline recovery code once you're back in control.

Neither command changes `local-workflows.sqlite3`, `public-github-sources.json`, GitHub permissions or already recorded workflow decisions. This is recovery of local login authority only.

## On-disk format and atomicity

- Legacy **v1 owner**: `{"version":1,"salt":"...","scrypt_hash":"..."}` remains valid. No data migration or forced code exists.
- Explicitly enrolled **v2 owner**: same fields and `recovery_salt`/ `recovery_hash`. The recovery verifier is SHA-256 of an independent random 16-byte salt and an unpredictable **32-byte random** URL-safe token. A 256-bit offline token does not depend on a user-selected weak secret.
- `LocalOperatorAuth.load` rejects unknown formats, malformed salts/hashes, symlinked owner files and insecure POSIX permissions.
- Enrollment and consumption use an **atomic same-directory replacement** of `operator.json` with an fsynced temporary mode-0600 file, then update in-memory state. A simulated disk-write failure preserves the previous working owner credentials and recovery state.
- Successful recovery writes a **v1 record** with a fresh random scrypt salt/hash and **no recovery verifier**. The same recovery token cannot be replayed. Successful authenticated password rotation similarly invalidates an already enrolled recovery code; enroll a new one afterwards.
- Only one recovery token can be active at a time. It is **not recoverable from its hash**; the user must save the original code. Losing both the current password and recovery code remains an unrecoverable state through supported product flows, by design.

## Security boundaries and known limitations

This is not a privileged identity proof: a malicious process with the same local OS user's full rights can already access the verifier, SQLite and application code. The CLI **is not a secure hardware-backed recovery solution** and the port-8765 check is process coordination, not a tamper-proof OS lock. The current shipping server uses fixed port 8765; do not add alternate production ports without strengthening mutual exclusion.

The recovery code is printed in the terminal on enrollment. Terminal scrollback, recordings, screenshots and cloud-synced shells can leak it. Prefer a local terminal and store it offline; no web UI should display it. No email, Telegram, GitHub issue, CI log, remote plugin or connected service should receive the code.

The code has **no expiry**, because offline owner recovery may be needed much later. Keep it private and rotate it when compromised. There is no recovery-code attempt throttle across separate offline processes; brute forcing a uniformly random 256-bit token is impractical, but this is not a substitute for strong OS account controls. The owner file does not encrypt local SQLite. Recovery also cannot repair a lost/corrupt owner file or inaccessible disk.

## Verification

`tests/test_local_offline_recovery.py` covers backward-compatible v1→v2 enrollment, 256-bit code uniqueness, single-use consumption, denial of invalid tokens, password constraints, re-enrollment invalidation, atomic storage rollback, authenticated rotation invalidation, corrupt v2 rejection, absence of any HTTP recovery endpoint, CLI roundtrip, and refusal to operate while local service port is busy.

Full Python 3.11/3.12/3.13 regression, Chromium browser acceptance, and both independent real public-GitHub read-only jobs remain required before merging. Actual E2E against a real customer/production PC is **not** claimed. This stage adds no external API writes, private GitHub permission, or automated background tasks.

The next critical gates before production data are stronger OS-scoped storage/locking and operator audit/recovery procedures; avoid calling single-user localhost password authentication a production security solution.
