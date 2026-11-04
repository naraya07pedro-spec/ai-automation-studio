# Post-Replace Durability and Local Operator Audit · Slice 46

Date: 2026-11-04. This document describes **specific observed failure modes** and the exact controls implemented, not a generic promise that local files or a Windows PC are secure.

## Risk corrected: post-rename directory fsync failure

In Slice 44/45, `secure_replace_json()` atomically replaced a sensitive file and then attempted POSIX directory `fsync`. If the second step failed, code returned a generic storage error but assumed the **old file remained intact**. In fact, `os.replace` may already have committed the new visible file. A loaded owner verifier or repo permission set could remain stale in memory, with dangerous disagreement about what the disk now contains.

Slice 46 introduces `PostReplaceDurabilityError` **only for failures after successful rename**. Before-rename errors retain the previous semantics: no old file truncation, no authorization state change. On a post-replace error:

- `LocalOperatorAuth` clears all sessions and enters a **restart-required fail-closed state**, refusing login, authorization, current-password validation, recovery-code enrollment and recovery reset within that process. The API returns a bounded `503 operator_restart_required` status, with no password/token detail. Restart the server and re-read the actual on-disk verifier to determine whether the new password, code state, or old password is current. Do **not** assume either state persisted after an interruption.
- `LocalPublicSourcePolicy` similarly enters a **restart-required deny-all state** and never allows an old permitted repo or existing GitHub preview snapshot to be used in the uncertain process. Grant/revoke returns a bounded `503 source_policy_restart_required`; after restart, the actual on-disk policy and epoch are loaded afresh. No privilege is silently inferred from stale in-memory state.
- Before-rename fsync/rename failures still return storage errors, without needlessly revoking valid sessions or permissions when the old file definitely remained intact.

A successful `os.replace` gives *atomic visibility*, not necessarily *crash durability* on all storage systems. POSIX directory fsync errors are treated as **unconfirmed** durability. Windows directory fsync is not implemented in this standard-library path, and **Windows power-loss durability has not been proven**. This change is a failure containment measure, not a guarantee of survival under abrupt power loss, bad disks or privileged local attackers.

## Local operator security event audit

`app/local_security_audit.py` adds a small, local SQLite log at `~/.ai-automation-studio/security-audit.sqlite3`. It has `synchronous=FULL`, serializes writes, owner-restricted mode on POSIX, and a finite retention policy (last ~5,000 events, pruning 500 old events upon reaching the limit). It records **only**:

- `seq` (monotonically increasing event sequence);
- UTC timestamp;
- one of `OPERATOR_LOGIN`, `OPERATOR_LOGOUT`, `OWNER_ROTATE`, `PUBLIC_SOURCE_GRANT`, `PUBLIC_SOURCE_REVOKE`, `RECOVERY_ENROLL`, `RECOVERY_RESET`;
- one of `ATTEMPT`, `SUCCESS`, `DENIED`, `FAILED`, `UNCERTAIN`;
- optional **16-character SHA-256 fingerprint** of the normalized public repo name for grants/revocations. No raw GitHub repo name is stored in this ledger.

Passwords, password hashes, session cookies, CSRF tokens, recovery codes/hashes, raw request bodies, IP addresses, GitHub issue text, owner identities and customer records **are not part of the event schema**.

An operator with a valid local session can inspect the last up to 100 events through `GET /api/operator-audit?limit=40` or **Recent security events** in the local UI. Anonymous callers receive 401. There is **no endpoint to edit/delete events**.

### Fail-closed audit behavior

Sensitive HTTP writes (password rotation, public GitHub source grant/revoke) first persist `ATTEMPT` to the local audit ledger. If audit storage fails, those actions are rejected with 503 **before mutation**. Login success/failure outcomes are also logged; if a login cannot be recorded, no new authenticated cookie is issued. Logout still revokes its session even if the audit has failed: it must remain possible to stop a valid session.

The HTTP handler writes a second `SUCCESS`, `DENIED`, `FAILED`, or `UNCERTAIN` event after a sensitive operation. A failure **after** the mutation may leave a logged ATTEMPT with no terminal outcome; this is deliberately visible and the audit store enters a restart-required state. **The audit is not a single atomic transaction with the JSON owner/policy file**. It is operational evidence, *not tamper-evident or forensic-proof*. A local adversary with the same OS account can still change or delete SQLite/JSON files. Avoid claiming more.

The offline recovery CLI also logs `RECOVERY_ENROLL` and `RECOVERY_RESET` attempts and outcomes in this same file, without ever logging the one-time code. The CLI keeps its local-port exclusion rule from Slice 45. **No external telemetry, email, GitHub issue mutation or cloud log transfer** is added.

## Read-only Windows ACL inspection

The new `scripts/check_windows_local_acl.ps1` is an **optional, non-mutating** PowerShell diagnostic for the existing local operator directory and four on-disk files. It reads ACL metadata only; it does not open file contents, adjust ACLs, require administrator elevation or send information to an external server.

From the cloned repo on Windows PowerShell:

```powershell
powershell -NoProfile -File .\scripts\check_windows_local_acl.ps1
```

The output flags broad well-known SID Allow ACEs for Everyone, Authenticated Users, built-in Users, Guests, and Anonymous. Exit codes: `0` no such broad entries detected (not proof of security), `2` broad access detected, `3` incomplete review, `4` not Windows. Missing files are marked NOT_PRESENT; there are no automatic permission fixes because altering ACL inheritance might break legitimate system access. The script itself has **not been executed on the user's Windows PC or certified in Windows CI**. It does not check every possible domain group, backup copy, encrypted-drive status, NTFS reparse point or privileged attacker.

## Verification scope

- `tests/test_local_security_durability.py` deliberately injects failure on first file `fsync` (before rename) and on second directory `fsync` (after rename), checks old-session revocation and deny-all public sources, and verifies disk reload chooses the currently visible verifier/policy. It also tests local audit content redaction, ACL mode on POSIX, retained event ordering/rotation, anonymous API denial, and fail-closed security mutations.
- `tests_browser/test_real_browser.py` adds an actual authenticated browser check of the redacted audit panel. The browser fixture uses isolated disposable SQLite owner/security stores.
- All existing Python 3.11/3.12/3.13 tests, real Chromium cases, and two distinct real GitHub public read-only jobs must pass on the **exact final PR HEAD**. A suite that only mocks remote responses does not count as live network evidence.
- A Windows-specific runtime ACL outcome, crash/power-loss survival, OS-compromise containment or cross-process atomicity across the SQLite and JSON databases is **not established** by Linux CI.

**Next practical gate:** add an OS-level mutual-exclusion / crash recovery strategy for the entire local server and a review of Windows ACL output collected on the actual PC. Before handling customer PII or third-party write-enabled agents, perform production threat modeling and independent security review.
