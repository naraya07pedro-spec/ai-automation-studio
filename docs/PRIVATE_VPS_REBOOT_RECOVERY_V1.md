# VPS reboot and task-persistence acceptance · 7 November 2026

## Why this exists

The previous deploy/verify_private_vps_runtime.py was a **single-host instant snapshot**: systemd active, localhost-only HTTP, correct security headers and API anonymous denial. Passing a snapshot could not establish that a deployed private service survives a Linux reboot, or that owner-approved recurring tasks remain in SQLite.

The new deploy/verify_private_vps_reboot.py runs in **two deliberate phases** on the **same actual Linux host**. It does not reboot the machine, restart or change systemd, install Ollama, create schedules, read task prompts or results, modify a user's app data or invoke a business connector. It uses the existing strict runtime snapshot and adds checks for systemd enabled state, dedicated unprivileged service-user identity, before/after Linux boot UUID, owner configuration fingerprint and unchanged existing recurring/AI schedule IDs.

**No credentials, task IDs, prompt text, workflow payloads, private IPs or provider secrets are printed or included in output.** The stored private baseline uses SHA-256 digests of owner config and schedule identifiers, counts of schedule rows and run histories, and one Linux boot ID. That baseline is not a remote attestation or independently verifiable cryptographic proof of Tencent identity.

## Prerequisites (on a verified, provisioned Linux VPS)

- Verify the VM status and real machine identity in Tencent Console (or another provider) and confirm its SSH host fingerprint from a trusted independent source. This tool does **not** log into cloud provider accounts.
- Install/start the existing reviewed private systemd service using docs/PRIVATE_VPS_ALWAYS_ON_V1.md and docs/PRIVATE_VPS_CREDENTIAL_READINESS_V2.md. The app remains bound to 127.0.0.1:8765 and must be accessed through the existing private SSH tunnel. Never open TCP port 8765 publicly.
- **Use the same dedicated NONROOT service account**, the User= value from ai-automation-studio.service, to run this script. Do not run the script as root; do not copy owner config files into your own user's home. The script explicitly checks that the current effective UID matches the systemd unit's configured User.
- To establish **schedule persistence**, a real owner-created recurring task must already be stored in recurring-schedules.sqlite3 or readonly-ai-schedules.sqlite3. This test does not add a fake task or modify active schedules. If both are absent or empty, it only proves post-reboot service and owner-config persistence, *not task persistence*. Creation of a legitimate recurring task must happen through authenticated owner UI, not via this utility.

## Capture, controlled reboot, verify

Before the operator's chosen maintenance reboot, while the service is healthy, in the checked-out project directory on the actual VPS as its app service user:

    git pull
    python3 deploy/verify_private_vps_runtime.py
    python3 deploy/verify_private_vps_reboot.py --capture-before-reboot

Capture succeeds only if systemd is enabled, service active, HTTP safe and anonymous APIs blocked, the current user equals the service User, and local owner configuration/any task SQLite files pass owner-only permission and read-only schema checks. It creates exactly one owner-only 0600 baseline:

    ~/.ai-automation-studio/live-acceptance/private-vps-reboot-baseline-v1.json

The baseline is **create-once** and will not be overwritten. It is local-private, but still records metadata and hashes: never commit or post the baseline file to GitHub/chat.

Next, the owner deliberately reboots the verified VPS **outside this script** during a maintenance window, following the cloud provider's approved reboot procedure. The script neither initiates nor automates reboot. Reboot interrupts any model inference, scheduled send and SSH tunnel; do not do it while a critical workflow is in flight. Any outage, provider billing and SSH access are the operator's responsibility.

Once the host is reachable again and the app service has had a chance to start, reconnect over independently verified SSH as the same nonroot service user:

    python3 deploy/verify_private_vps_runtime.py
    python3 deploy/verify_private_vps_reboot.py --verify-after-reboot

The verification **fails closed** if Linux boot ID is unchanged, the systemd unit is disabled/inactive, the app is no longer private/authenticated, the effective user does not match systemd, owner config fingerprint differs, a previously existing schedule database disappears, schedule ID sets differ, or any historical run count decreases. It intentionally allows new run-history rows to appear while preserving the same schedule IDs.

## Interpreting output and exit codes

- Exit 0 and status PRIVATE_VPS_REBOOT_BASELINE_SAVED: a valid pre-reboot snapshot was recorded. **Not a success-after-reboot claim.**
- Exit 0 and status LIVE_PRIVATE_VPS_REBOOT_AND_SCHEDULE_PERSISTENCE_PASSED: the *host where the script ran* had different Linux boot IDs, protected authenticated service and enabled unit in both phases, preserved owner config and at least one actual existing recurring schedule's identifier across reboot, and did not lose recorded run-history counts. It is not proof that the unit autostarted without anyone intervening, nor that an overdue task executed after reboot.
- Exit 3 and status LIVE_PRIVATE_VPS_REBOOT_SERVICE_ONLY_PASSED: service and owner config survived a changed boot, **but there were zero stored schedules** to prove schedule persistence. Do not promote this partial outcome to full acceptance.
- Exit 2 and status NOT_VERIFIED: preflight failed, baseline absent/unsafe, still same Linux boot, changed/missing state, invalid schema, wrong service account or other safety check rejected the result.

The script never prints raw user prompts, keys, token material, task identifiers or full system paths. It does not use HTTP cookies, cloud credentials, SSH private keys or environment tokens. A local privileged actor who can modify the baseline or both SQLite files could forge or restore state; this is **not tamper-proof remote attestation**.

## Important limits and next actual acceptance

This check **does not** verify Tencent billing account/VM ownership, source IP restrictions, automatic service activation without manual intervention, the worker executing a new due-slot after reboot, 24-hour continuous uptime, local model inference, Google OAuth enrollment, n8n provider completion or backup/offsite recovery. For local Ollama due-slot acceptance, use the **separate** tools/verify_live_ai_schedule.py from PR #104; that tool uses a disposable model schedule and does not test the production Studio service. For downstream business outcomes use the separately approved n8n final-step proof flow, never HTTP 2xx alone.

This version does not collect resource specs or judge whether Tencent trial hardware can host an Ollama model. Verify available RAM, CPU, disk and swap independently before changing from offline to local-ai mode.

After verification, preserve or independently back up the private baseline using an operator-managed secure encrypted storage method. A later maintenance cycle requires **explicit review and deliberate removal** of the old baseline file before capturing a new one. The script never silently overwrites evidence.

**CI evidence:** tests/test_private_vps_reboot_persistence.py uses disposable SQLite, synthetic Linux boot IDs and file permissions. CI completion is evidence of tested code behavior only, never that a real user's Tencent VM rebooted. See docs/PROGRESS.md for PR/head SHA, CI status and unverified live deployment claims.
