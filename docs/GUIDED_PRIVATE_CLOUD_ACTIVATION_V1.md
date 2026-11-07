# Stage #110 · Guided first-time PRIVATE cloud-only activation (no Ollama)

## Why this changes the critical path

The main technical blocker after PR #109 is first-party **execution on the real
Tencent Linux VPS**. CI cannot connect to the user's cloud account or create a
real OpenAI response. This stage removes repetitive operator setup steps by
orchestrating the **existing** provisioners, private systemd installer, and
on-host verification under explicit interactive consent. It is NOT another
synthetic claim of live availability, an SSH credential import, or a cloud
provider management integration.

## Existing operator must still perform the first SSH login

From Tencent's official console, independently confirm that the instance is
actually running, its OS supports systemd, its SSH address is correct and the
SSH host-key fingerprint matches the provider console. Ensure SSH is allowed
from a trusted address but **never open TCP 8765 to the internet**.

Connect normally as a **nonroot Linux account that may use sudo**.
On that host, install Python 3.11+, Git and sudo using trusted OS packages,
then clone this repository if needed:

    git clone https://github.com/naraya07pedro-spec/ai-automation-studio.git
    cd ai-automation-studio

Do not run the activation program on Windows or a random CI runner. Review the
checkout (and in particular any commands run through sudo). Never blindly run
code from an unreviewed remote branch. The activation script requires a **clean
main** checkout containing the verified PR #109 merge commit. It does **not**
call `git pull` or overwrite uncommitted files.

### Step A: completely read-only preflight

    python3 deploy/activate_private_cloud.py

This returns JSON: `READY_TO_APPLY_WITH_OWNER_CONSENT` or `BLOCKED` with
individual checks. It makes **no system changes**, does not read the API key,
and never logs in to Tencent or OpenAI. Main preflight checks include Linux
systemd, actual nonroot UID/home, Python, clean main checkout that includes
PR #109, existing owner-storage permission safety, a root-owned/non-group-writable Python interpreter for sudo (a user-owned virtualenv is refused), no installed Studio unit,
port 8765 currently free, and installed sudo.

### Step B: guided first-time activation

Only for a **new** systemd installation with no existing Studio service:

    python3 deploy/activate_private_cloud.py --apply

Read the warning and type `ACTIVATE_PRIVATE_CLOUD` exactly. Only after that:

1. If no owner identity exists, run the existing `--init-owner` entrypoint
   using its interactive private password prompt. Never reset an existing owner.
2. Authenticate sudo in the interactive VPS terminal. If the root-owned OpenAI
   key file is missing, run the existing hidden-prompt credential provisioner.
   If already present, preserve it. The actual installer validates permissions
   and key format. **Do not paste the key in this chat, GitHub or shell args.**
3. Render/preview the existing fixed cloud-only systemd unit, then use its
   explicit `--install` command. Never modify an existing unit. Verify the
   private HTTP/auth probe and the **live** cloud-only MainPID process flags.
4. Report `PRIVATE_CLOUD_SERVER_BOOT_VERIFIED_NO_MODEL_REQUEST` only if
   the real local systemd process and private HTTP service pass. Failures remain
   `BLOCKED` or `DEPLOYMENT_INCOMPLETE_REVIEW_SYSTEMD`, never disguised as
   success.

The activator **does not create any schedule or issue a provider request**.
Supplying an API key alone does not authorize billed inference. Even when
the process boots successfully, no provider account/price/reboot/24h claim is
made. The first-time path refuses to overwrite an existing systemd unit; for
upgrades use the already documented operator-managed backup and review process.

### Step C: independent real-model acceptance (separate owner action)

Use the existing Windows `CONNECT_PRIVATE_VPS.cmd` SSH tunnel, log into
the owner-protected `/mission` panel at local 127.0.0.1:8765, and create
exactly **one** nonsensitive OpenAI Responses text task with one run,
one-day expiry, one-minute cadence, and explicit paid API consent.
Choose only a model ID actually enabled in the user's separate API account.
Review the result/token usage, then on the VPS as the same nonroot service user:

    python3 deploy/verify_private_cloud_ai_runtime.py

Its maximum successful result is `CLOUD_RUNTIME_RECEIPT_OBSERVED_REVIEW_REQUIRED`.
SQLite/local receipt shape is NOT independent proof of a billable provider
response. Reconcile real input/output usage and cost in the actual provider
console. Never auto-retry an uncertain call before inspecting provider usage.
The ChatGPT subscription is not an OpenAI API payment entitlement.

### Failure and recovery

- `BLOCKED` preflight: inspect the false boolean; solve that exact blocker
  first. An absent owner is allowed; insecure owner storage is not.
- `service_or_port_changed`: another process/service took the port, or an
  existing unit appeared; stop without replacing anything.
- `credential_provision_failed`: fix root-only ownership/credential issues
  manually. Existing credentials must not be silently overwritten.
- `DEPLOYMENT_INCOMPLETE_REVIEW_SYSTEMD`: after attempting the install,
  inspect `systemctl status ai-automation-studio.service --no-pager` and the
  redacted journal locally. A failed service install can leave a unit present
  and the next run will properly refuse an overwrite.
- Successful boot alone does not establish Tencent identity, paid model
  execution, restart persistence, 24-hour uptime, backups or 24/7 availability.

## CI evidence boundary

Tests cover opt-in, no root actions on an unapproved/blocked path, refusal to
overwrite an existing unit, failure before sudo, reuse of an existing key,
exact invocation of existing scripts, safe owner permissions and output
redaction. GitHub CI runs these using mocks and ephemeral filesystem fixtures
on Python 3.11, 3.12 and 3.13: no sudo, paid API calls, Tencent SSH, model
inference or actual service installation occur in CI.

Do not mark `LIVE_OPENAI_MODEL_VERIFIED` or `ONLINE_24_7_VERIFIED` true
until the VPS and provider evidence required by Stage #109 exists.
