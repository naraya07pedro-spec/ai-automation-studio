# On-host post-reboot local rule execution with systemd worker provenance · 2026-11-07

## Why this milestone is not another fake VPS green check

PR #105 can show two distinct Linux boot IDs, an enabled and active private Studio systemd service, protected localhost port, and preserved schedule IDs. It **cannot** prove that an actual scheduled job ran after the reboot. More dangerously, a new SQLite row with a recent timestamp alone could be a manual call, or the completion of a due slot claimed before the reboot.

This release adds **observational worker-origin data** to *completed local deterministic rule receipts* (not model inference and not n8n delivery). The owner can run a new read-only verifier after the original PR #105 baseline/reboot checks and look for a genuinely new post-boot due slot tagged with the currently running systemd service invocation.

The verdict is deliberately narrow: the host's local scheduler ledger contains a completed local rule evaluation after the new kernel boot, with matching boot UUID and service InvocationID captured by the worker process. This is **not cryptographic remote attestation** or proof that the service started autonomously after boot, and the administrator/DB owner could fabricate a receipt.

## What changed in code

- app/local_recurring_scheduler.py: when the actual background worker calls run_due(), it uses a private in-process token and records validated 32-hex systemd INVOCATION_ID and Linux /proc/sys/kernel/random/boot_id onto the completed deterministic run. Manual run_due() callers receive NULL provenance. Windows/local non-systemd processes remain unproven by design.
- Existing SQLite databases migrate in place by adding completed_by_invocation and completed_boot_id to recurring_schedule_runs. Existing history is preserved, and older rows intentionally retain NULL provenance rather than being falsely reclassified as systemd-produced.
- deploy/verify_post_reboot_rule_run.py: strictly **read-only** on-host checker. Reuses verified PR #105 private before/after reboot baseline, nonroot service identity, systemd active/enabled, protected localhost, owner config and unchanged schedule IDs. It then checks the real kernel boot timestamp from /proc/stat, the current systemd InvocationID, and the existing SQLite completed-run rows for same invocation and boot. It requires the job to have existed before the original baseline, the **scheduled slot itself** to be strictly later than kernel boot (excluding old work replayed after reboot), and a completed timestamp later than that slot and not in the future.
- The verifier re-checks the active systemd invocation and protected service after reading a receipt, rejecting results if the service was restarted or the schedule set changed during the check. No raw schedule IDs, prompts, provider tokens, decision payload or systemd invocation identifier are printed.

## Exact owner action on an already provisioned and verified Linux VPS

1. Confirm the real VM in Tencent Console, including Linux OS, SSH identity and its Running state. Existing app must be installed as private systemd service with User= a dedicated **nonroot** account. This milestone does **not** remotely connect to Tencent or change the instance.
2. Update the reviewed repository as that nonroot service account. Arrange a **deliberate local-only deterministic rule schedule** through the existing authenticated Mission Control UI before reboot. Prefer a safe 1-minute repeat interval. Ensure the selected workflow's policy returns only local rule results. If the existing owner-armed webhook autopilot uses that schedule, pause/stop its external dispatch first: this checker itself does no external send, but the existing normal worker may continue approved sends.
3. For proper origin stamping, the systemd Studio service must be running the reviewed code **before the post-reboot due slot**. A code update alone does not restart an already running service. Use an independently reviewed maintenance deployment/restart procedure; this script never modifies the unit and never performs restarts. A service not actually launched by systemd, or a unit lacking INVOCATION_ID, fails closed.
4. Before intentionally rebooting during an approved maintenance window, as the exact service user:

       git pull
       python3 deploy/verify_private_vps_runtime.py
       python3 deploy/verify_private_vps_reboot.py --capture-before-reboot

   Existing baseline files are create-once. If one already exists from a different maintenance window, explicitly inspect and archive it outside Git before deliberately starting a fresh baseline cycle. Do NOT silently overwrite it.
5. Perform the real cloud reboot **outside the scripts**, after checking there are no critical in-flight tasks. Reconnect via trusted SSH after the host comes back. The new unit must be truly enabled across boot.
6. Still as the same service user, verify both persistence and execution:

       python3 deploy/verify_private_vps_reboot.py --verify-after-reboot
       python3 deploy/verify_post_reboot_rule_run.py --wait-seconds 180

   The verifier polls only the owner's local SQLite ledger (up to 180 seconds) and does not trigger due tasks. The Studio worker must perform the job naturally; a 1-minute cadence is recommended to exercise an eligible new post-reboot slot. Immediately after boot, the old next_due slot may precede boot and be recovered first; the verifier **rejects that historical slot** and requires a genuinely new slot after boot.

## Interpretation and guardrails

- Exit 0 and LOCAL_RULE_POST_REBOOT_SERVICE_INVOCATION_OBSERVED: a local deterministic rule was completed in a scheduled slot after kernel boot and marked with the *current* systemd service InvocationID. Owner config, private API, and existing schedule set also passed the previously captured reboot baseline gate.
- Exit 3 and POST_REBOOT_LOCAL_RULE_EXECUTION_NOT_YET_OBSERVED: protected service survives and schedule IDs persist, but there is no qualifying post-reboot completed local rule receipt yet. Not a production success; verify the actual due time and active rule schedule. A paused or long-cadence schedule may require manual owner review, not a forced retry.
- Exit 2 and NOT_VERIFIED: no qualifying live host prerequisites, missing/unsafe baseline, mismatched invocation/boot, missing provenance columns, DB permission/schema error, clock inconsistency, or another fail-closed condition.

This tool does not install a model, create a task, open a public port, authenticate to Tencent, send n8n requests, contact Gmail, invoke a provider or reboot/restart a VM. It reads only the owner's previously provisioned local scheduler database and fixed systemd/kernel properties. There is no full backup/restore here. Do not place credentials in prompts or CLI parameters.

**A strong limitation:** the invocation marker is observational, not signed or independently attested by the cloud provider. An actor with write access to SQLite can forge its fields, and a manually restarted systemd service can produce a valid observation. Therefore a positive result is **evidence of recorded local execution in the inspected service invocation**, not proof of unattended cold boot, workload correctness for AI, external side effects, continuous uptime or Tencent VM identity.

## Distinct acceptance results that remain separate

- Real installed Ollama inference and disposable scheduled model persistence: tools/verify_live_ai_schedule.py (PR #104).
- Private systemd/loopback snapshot: deploy/verify_private_vps_runtime.py.
- Distinct Linux boot and preservation of owner/schedule DBs: deploy/verify_private_vps_reboot.py (PR #105).
- **New:** actual local deterministic rule slot after boot, matching current service worker invocation: deploy/verify_post_reboot_rule_run.py.
- External n8n business completion: only after independently examining actual n8n Executions and final-step HMAC proof, not from rule slot or HTTP 2xx.

GitHub CI uses synthetic boot/InvocationID values and real disposable SQLite, never the owner's Tencent VPS. Exact PR/CI final results are in docs/PROGRESS.md.
