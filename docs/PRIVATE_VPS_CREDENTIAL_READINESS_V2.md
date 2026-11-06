# Private Tencent/Linux VPS operational readiness v2 · 2026-11-06

## Purpose and actual boundary

This release closes the gap between PR #91's private systemd/SSH-tunnel deployment and PR #95–#99's opt-in encrypted webhook/Gmail connectors. The prior VPS service unit only enabled local read-only/offline features. Now its installer can **explicitly include** existing encrypted managed-webhook, native Google Gmail metadata-only, and the single-destination n8n outbound webhook feature flags.

Secrets are **never inserted into systemd unit source, GitHub, command-line arguments, shell history or a world-readable environment file**. The unit uses **systemd LoadCredential** with fixed root-only source paths. The existing server reads only an allowlisted vault key, webhook URL and optional signing secret through systemd's per-service runtime credential directory. Ordinary Windows/private interactive environment-based startup remains supported unchanged. An ambiguity between process environment and systemd file for the same secret fails closed.

The actual owner Tencent Cloud instance has **not been connected to, changed, billed, inspected or rebooted** by this implementation. This code runs only after the user installs it on a real, eligible Linux systemd host. The read-only acceptance probe below is designed to produce first-party evidence when someone actually runs it there. CI uses synthetic hosts, not proof of Tencent availability.

## What is new in repository

- \`app/local_service_credentials.py\`: strict 3-name allowlist, read-only systemd credential files, rejects symlinks, group/world-readable modes, oversized and multiline contents. No secrets in normal logs.
- \`deploy/private_vps_service.py\`: additional explicit opt-in \`--enable-encrypted-managed-webhooks\`, \`--enable-google-gmail-metadata\`, \`--enable-webhook\`, optional \`--enable-webhook-signing\`. Produces \`LoadCredential\` directives only when needed. Before a **new** service installation, validates exact root-owned source files, 0600 permissions, a valid base64url 256-bit vault key and a basic HTTPS webhook URL.
- \`deploy/provision_private_vps_credential.py\`: privileged terminal **hidden prompt**, fixed names, root-only 0700 directory and 0600 file, exclusive create (never silently overwrite).
- \`deploy/verify_private_vps_runtime.py\`: **read-only local live snapshot**: systemd service active, listener exists on exactly IPv4 127.0.0.1:8765 with no wildcard/IPv6 listener on port 8765, Mission Control responds with protective headers, and a private schedules API denies anonymous requests. Returns structured redacted JSON and exit 0 only if all checks pass.
- \`tests/test_private_vps_credentials_and_probe.py\` and dedicated CI workflow: local credential permissions, settings isolation, opt-in flag safety, systemd unit verification and acceptance probe rejection tests.

## Exact once-only setup, after real VPS access is independently verified

1. Confirm the Tencent VM is actually **Running**, Linux with systemd, has a valid SSH host fingerprint and an owner-controlled non-root app user. Do not enable public TCP 8765; allow SSH only from trusted addresses. Log into the server over your normal independently verified SSH route. See original [private VPS runbook](PRIVATE_VPS_ALWAYS_ON_V1.md) for clone, non-root user, Python 3.11+, and owner-password provisioning.
2. Update the reviewed Git checkout as the unprivileged service user. Provision the Studio owner password as that user via \`python3 app/local_server.py --init-owner\` if not yet created. Do not reinitialize existing owner data.
3. If using encrypted managed webhooks or Gmail, install \`python3 -m pip install -r requirements-vault.txt\` in the app's intended Python environment. The SAME encryption master key used for the Gmail encrypted grant on the PC must be privately provisioned on the VPS. Do NOT create a new key or paste it in this chat.
4. On the VPS, from the reviewed checkout, run these **only for connectors you intend to enable**, using the hidden prompt; the typed value never appears in the command arguments:

   \`\`\`bash
   sudo python3 deploy/provision_private_vps_credential.py --name vault-key
   sudo python3 deploy/provision_private_vps_credential.py --name webhook-url
   # Optional, only if signing existing outbound webhook requests:
   sudo python3 deploy/provision_private_vps_credential.py --name webhook-signing-secret
   \`\`\`

   Source files reside in \`/etc/ai-automation-studio/\` (0700, root-owned); each source is 0600. Unlike authenticated encryption of connector registry JSON, **these root-only source credential files themselves are not encrypted at rest**. Root or a compromised VPS administrator can read them. Use encrypted disk/backup where practical, and rotate credentials after exposure. \`LoadCredential\` provides service-process isolation, not hardware key custody.

5. For an **initial installation only**, preview the unit and then deliberately install it. Example for private offline rule scheduler with encrypted managed n8n connections and Gmail metadata status, but NOT automatically sending to n8n:

   \`\`\`bash
   cd ~/ai-automation-studio
   sudo python3 deploy/private_vps_service.py --user YOUR_NONROOT_USER --checkout "$PWD" --mode offline --enable-encrypted-managed-webhooks --enable-google-gmail-metadata
   sudo python3 deploy/private_vps_service.py --user YOUR_NONROOT_USER --checkout "$PWD" --mode offline --enable-encrypted-managed-webhooks --enable-google-gmail-metadata --install
   \`\`\`

   To include the already existing **owner-armed scheduled single n8n destination** functionality as well, additionally pass \`--enable-webhook\` (and \`--enable-webhook-signing\` only if its separate credential is provisioned). Without an owner-created/armed finite autopilot policy, no external scheduled sends are authorized. Encrypted multi-connector registrations are **not** automatically granted scheduled dispatch.

   **Important:** the service installer intentionally refuses to overwrite an existing unit, so do not run \`--install\` on an already-installed VPS expecting an upgrade. Inspect and explicitly plan a controlled administrator service-unit change, backup, daemon-reload and restart. Never silently replace a running service file.

6. Gmail connection is **not established merely by adding the flag**. The one-time Desktop OAuth grant is enrolled on a PC with the official Google consent browser. With owner approval, transfer the existing encrypted \`google-gmail-vault.json\` to the **non-root service user's** \`~/.ai-automation-studio/\` on the VPS via a trusted encrypted channel such as verified SSH/SFTP, keeping owner-only file permissions. The master key on VPS must match the one that encrypted the grant. The application does not copy Google tokens from connected ChatGPT Gmail plugins, and a Gmail metadata read still requires owner login, CSRF and a fresh owner password. No Google account email or token is part of CI.

## Read-only verification on the actual VPS

Run after the service is installed and started:

\`\`\`bash
python3 deploy/verify_private_vps_runtime.py
\`\`\`

Output is JSON containing \`status\`, five boolean checks and an explicit limitations statement. Exit code 0 and \`LIVE_PRIVATE_VPS_SNAPSHOT_PASSED\` mean the inspected **current Linux host**, at that instant, passed a local systemd, bind, HTTP and anonymous-auth boundary check. They DO NOT independently prove that it is the user's Tencent VM, survives a cloud reboot, has an authorized Google account, can run Ollama, executes a scheduled business task, or delivered an n8n payload downstream.

For complete live acceptance, separately obtain independent Tencent Console instance identity/running state, SSH tunnel from Windows, app login, stable schedule receipt after controlled service restart, and downstream n8n receipt if that capability is enabled. No real business writes or restarts should be triggered merely to claim a CI milestone.

## Remaining explicit gaps

- This implementation is **not** an automatic Tencent deployment, public login site, HA cluster, full encrypted backup/restore, AWS/GCP/Tencent KMS, Google OAuth broker or general MCP plugin marketplace.
- Gmail is **metadata-only**. Outbound n8n is the existing independently owner-armed single destination; the managed encrypted connector registry remains manual-send unless separately authorized in a future scoped feature.
- \`--mode offline\` runs local SQLite deterministic scheduler/workflows without Ollama, but AI inference requires an independently installed/model-verified Ollama runtime on the VPS; do not infer it from service liveness.
- systemd LoadCredential root source files should be backed up/rotated under a secure, documented policy. No backup scheduler or offsite recovery drill is introduced here.
- No claim of Tencent VM runtime verification or provider consent until actual owner-visible evidence is collected.

This is an **operational readiness + acceptance** release, not a live service verification report.
