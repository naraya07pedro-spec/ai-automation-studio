# Operator Password Rotation and Public Source Authorization · Slice 44

Date: 2026-11-04. Intent: complete the next operational security boundary for the existing **single-user localhost workflow app**, rather than implement more synthetic workflow demonstrations.

## Password rotation (authenticated and reauthenticated)

1. Provision one owner once with `python app/local_server.py --init-owner` (existing Slice 43 requirement). Start the local server, log in, and open **Change operator password**.
2. Supply the **current** password and a **different** new 12–128-character password. A valid operator session and CSRF token are necessary but insufficient; the correct current password is required again. Bad current passwords share the same five-attempt/60-second lockout as login.
3. The server creates a new random salt/scrypt digest, atomically writes the existing `~/.ai-automation-studio/operator.json` file using a temporary mode-0600 file, flushes/syncs it, then replaces the old verifier. **Only after the durable replace succeeds** does it update the in-memory verifier and revoke **every** in-process session. The browser clears its credential context and displays the login gate.
4. Old cookies and the old password stop working on that server. The new password works immediately and survives server restart. Existing SQLite workflow data, decisions and histories remain unchanged.
5. If disk persistence fails, the old owner verifier and current sessions remain valid, and the server returns a non-identifying storage error. Passwords/verifiers are not logged or returned by the API.

Endpoint: `POST /api/operator-rotate`, exact JSON body `{"current_password":"...","new_password":"..."}`, with normal session cookie and `X-AAS-CSRF`. The login/health shell remains available after session revocation.

**Recovery limitation:** This stage provides authenticated rotation, **not lost-password recovery**. There is deliberately no anonymous HTTP password reset, email backdoor, default credential, insecure recovery question or automatic deletion of the owner file. Losing the current password without a separately implemented recovery capability still requires a careful future recovery design. Existing Slice 43 owner files remain backward compatible without migration.

## Public GitHub allowlist, explicit per-repository permission

The read-only GitHub API adapter already supports strict `owner/repo` validation and fixed `api.github.com:443`. **Slice 44 makes the server default-deny**:

- With no grant, even a successfully logged-in owner receives `403 public_source_not_allowed` **before an outbound GitHub request**. There are no baked-in trusted external repositories in the shipping server. The public synthetic fixture #47 is only a suggested source and **must also be explicitly granted** by the operator.
- From **Allow a specific public GitHub repository**, the owner enters an exact public `owner/repo` and re-enters the **current local password**. This uses the shared login lockout, the session cookie and the CSRF header; there is no silent, session-only one-click grant.
- An allowlist of at most **16 normalized public repositories**, stored at `~/.ai-automation-studio/public-github-sources.json`, is persisted using atomic file replacement and restrictive POSIX permissions. Empty/missing file means deny-all; malformed, symlinked or insecure-policy files cause production startup to fail closed rather than open access.
- `GET /api/local-github-sources` requires an owner session and lists only approved public repository names. `POST /api/local-github-source-grant` and `POST /api/local-github-source-revoke` require the exact body `{"repo":"owner/repo","current_password":"..."}`, session, CSRF, and reauthentication.
- On preview, repo permission is checked **before** contacting GitHub and after fetching provider metadata. An allowlist **epoch** is recorded with the ephemeral snapshot. Revocation blocks new reads and invalidates any uncommitted snapshot, including if the repository is later regranted. Source authority remains locked through actual SQLite commit to avoid a grant/revoke race.
- Other local non-GitHub workflows and manually entered categorical CSV events remain governed by the local owner session; revoking a GitHub source does not erase or rewrite previously committed SQLite events.
- Public allowlist grants do **not** grant a GitHub OAuth scope, credential, repository write, issue creation, private repository access, webhook, email send, external dispatch, or AI autonomy. The adapter remains unauthenticated *to GitHub* and **read-only**.

**Note on platform security:** Windows ACL inheritance is outside this code's formal assurance. POSIX owner-file/directory permission hardening is implemented; Windows users should ensure their local account/home directory is protected. A malicious process running as the same OS user may read the files or call localhost and is outside this single-owner prototype threat model.

## Test and verification gates

`tests/test_local_owner_rotation_source_policy.py` covers correct/incorrect password changes, storage failure rollback, session revocation, restart, shared lockout, allowlist persistence, malformed/invalid repo denial, permissions, full allowlist refusal, reauthentication for source grants, CSRF and Origin blocking, no outbound fetch before grant, snapshot invalidation after revoke/regrant, and exact previously approved local writes. The existing backend suite remains required.

`tests_browser/test_real_browser.py` covers real browser reauthorization for grant/revoke and the full password rotation → session revoked → rejected old password → accepted new password sequence, using a disposable test-only owner password that is restored within the fixture. All prior browser tests remain in place.

Both independent **real GitHub public network CI** jobs, including issue #47 → SQLite `:memory:`, are still required. The CI live probe uses a standalone no-HTTP test harness; this **does not weaken the shipping local server's default-deny policy**. Browser test fixtures may explicitly allow one sample test repository without granting that source on production startup.

**Non-goals:** no user accounts beyond one operator, no passwordless recovery or MFA, no TLS/public hosting, no private provider credentials, no customer-data processing, and no external effects. Prioritize recovery design and stronger OS-level protections before private or business-critical data.
