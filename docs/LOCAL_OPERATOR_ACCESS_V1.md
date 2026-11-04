# Single Local Operator Access Gate · Slice 43

Date: 2026-11-04. This scope deliberately protects **one operator's local API access** before permitting future private/customer integration. This is not multi-user IAM, online authentication, database encryption or secure public SaaS.

## One-time offline setup

Run `python app/local_server.py --init-owner` on the intended computer. The CLI uses `getpass` twice (12–128 characters, no control characters), creates `~/.ai-automation-studio/operator.json` with atomic `O_EXCL` and restrictive file/directory permissions on POSIX, and saves **only** a random-salted scrypt password verifier (`N=16384,r=8,p=1`), not the plaintext password. Existing owner files are not overwritten by CLI. No HTTP signup or password-change endpoint exists.

Normal `python app/local_server.py` must load a valid owner file **before binding port 8765**. Missing, symlinked, invalid or insecure-permissions owner config is rejected, with a setup command. Existing `local-workflows.sqlite3` is **not overwritten or migrated** by login setup. The owner setup does not grant privileges to remote GitHub, Google, mail, CRM or other systems.

There is **no supported password recovery or owner rotation mechanism** yet. Back up the owner verifier with OS-level protection and do not reuse sensitive passwords or put secrets in issue labels. If the verifier is lost, the operator will need a deliberate recovery workflow; do not auto-reset or auto-provision over HTTP.

## Authentication and permissions

- `POST /api/operator-login` accepts exact JSON `{"password": "..."}`. Failed credentials return a generic 401, with an in-memory 60-second lockout following five consecutive failed attempts. The server never logs or echoes passwords. On success, it returns a random session CSRF token in a same-origin JSON response, sets a random opaque 256-bit token cookie `aas_session`, `HttpOnly; SameSite=Strict; Path=/; Max-Age=3600`, and exposes no reusable account credential to JavaScript.
- `GET /api/operator-session` checks the cookie and returns `authenticated: false` for visitors or a CSRF token and remaining lifetime for the valid operator (needed for reloads).
- `POST /api/operator-logout` requires both the session and CSRF, consumes the server-side token and clears the cookie. All sessions live **only in process memory** and expire after one hour or on server restart. Bounded to eight concurrent sessions; an excess login evicts the oldest.
- All existing mock POST actions, local create/activate/run/stage/publish/pause/resume, CSV preview/commit and public GitHub preview/commit require a valid session **and** an `X-AAS-CSRF` header matching that session. `GET /api/local-workflows`, `GET /api/local-history`, `GET /api/local-revisions`, template listing, and guided setup choices also require a valid session. The bare HTML login page and minimal `GET /api/health` remain reachable before login. Errors do not reveal stored data.
- All operations also retain existing exact localhost Host and Origin restrictions, JSON content-type/body-size limits and no-store headers. Login does not bypass these controls. SameSite/Origin+CSRF make basic cross-origin attacks harder, but do not substitute for an authenticated transport with stronger process isolation.

No role matrix is needed for one owner. Scope is `LOCAL_OPERATOR` only: authenticated owner may use the existing local workflow operations and already-scoped public read-only GitHub connector. **There is no privilege for real external writes, sending, private third-party data or autonomous background tasks.**

## Browser implementation

`web/index.html` displays a login gate by default, attempts session restore with the cookie on load, and exposes a lock/logout action. Every pre-existing POST, including the five mock demo actions, carries `X-AAS-CSRF`. Operator password is cleared after successful login, token is held in JS memory only (not localStorage/sessionStorage), cookie is HttpOnly; reload reads the current session state from the server. Failed login leaves the workbench locked. Direct HTTP requests remain protected independently of any visual overlay.

## Actual threat boundary, caveats and red-team cases

This reduces **unprivileged localhost network callers** from reading histories, previewing external sources or committing local data. It does **not** protect from a compromised operating system, malware under the same OS account, a privileged process able to read the verifier/SQLite or tamper with source code, or local traffic interception. Loopback uses HTTP, so the session cookie **cannot carry the Secure attribute**. There is no TLS, user provisioning portal, recovery flow, TOTP/WebAuthn, multi-user permissions, external secret vault, SQLCipher, host sandbox or public-network hardening. Never port-forward or deploy this localhost server publicly, and do not process real customer PII.

`LocalOperatorAuth.provision` is offline only. The production app server uses `LocalOperatorAuth.load` and fails closed; tests call `create_handler(operator_auth=None)` only in controlled isolated integration fixtures for historical deterministic workflows, never as the shipping entrypoint. The browser suite uses `tests_browser/serve_auth_fixture.py` with a test-only ephemeral password, never the production server. Production has **no hardcoded or environment-variable bypass** to disable authorization.

## Acceptance

- `tests/test_local_operator_auth_http.py`: owner file permissions and strict nonoverwrite, salted verifier secrecy, 401/403/429 responses, lockout/expiry, bounded session store, denial of unauthenticated or CSRF-less requests across **all** local and mock mutations, no GitHub provider fetch on denied calls, denied unprivileged history, and logout revocation.
- `tests_browser/test_real_browser.py`: existing functional browser journeys now start with a real login against an authenticated fixture server, then verify retained UI/HTTP behavior plus session restore, logout, wrong password and missing CSRF denial.
- Existing 521 Python tests/interpreter and 24 browser tests must stay intact. New offline and browser tests are regression evidence, not proof of real customer data isolation or production deployment.
- The independent GitHub public read-only transport and exact owned issue #47→ephemeral SQLite jobs continue running. They use a standalone non-HTTP probe and do **not** bypass or weaken the shipping localhost login requirement.

**Next stage:** user-controlled password rotation/recovery, scoped operator audit/session revocation, and explicit consent/provenance constraints for real data. Do not add provider write capabilities until operator permissions and effect reconciliation are designed.
