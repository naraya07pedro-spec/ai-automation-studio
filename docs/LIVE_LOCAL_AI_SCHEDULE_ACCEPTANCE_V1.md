# PR #104: On-host real Ollama schedule acceptance

## Difference from earlier test suites

PR #103 shipped a real SQLite Ollama schedule worker but GitHub CI used an INJECTED model fixture, never the owner's Ollama or Tencent instance. This release gives the owner a **deliberately on-host live** acceptance command. Its genuine entrypoint calls the real Ollama catalog and fixed-loopback local run_agent transport, starts the actual one-minute AI scheduled worker, waits for a real due time, checks a bounded nonempty model answer, reopens the SQLite state, and proves no duplicate model dispatch after reopen. It uses a disposable temporary schedule, never the user's live Studio tasks, credentials or connector writes.

## Windows: exactly how to run

1. Update the reviewed checkout (git pull), install/start Ollama separately and choose a model actually installed on the computer. On an 8 GB Windows host prefer a small model that fits memory and can finish bounded inference. This tool never installs or downloads a model.
2. Stop any running Studio server/listener on port 8765 before the isolated acceptance. The script refuses concurrent Studio processes to avoid resource and model-gate contention.
3. In PowerShell in the repository directory, run:

    python app/local_ollama_readiness.py
    python tools/verify_live_ai_schedule.py --model YOUR_INSTALLED_MODEL --save-redacted-evidence

Replace YOUR_INSTALLED_MODEL with a name printed by the first command. The second command waits for a genuine wall-clock one-minute slot and model completion. Neither command requires Tencent, n8n, Gmail or provider credentials. No arbitrary website, file read or external API send is permitted.

Success produces JSON status LIVE_LOCAL_OLLAMA_SCHEDULE_ACCEPTANCE_PASSED and inference_source REAL_LOCAL_OLLAMA_127_0_0_1_11434; verifies worker dispatch, bounded result, IDLE checkpoint, exact SQLite readback, and no replay. It explicitly says Tencent identity, service reboot, user workflow execution and 24-hour uptime are NOT verified. Failure prints NOT_VERIFIED, exits nonzero and never falls back to an injected model.

Optional private report file (create once, never overwrite): ~/.ai-automation-studio/live-acceptance/live-local-ollama-TASK_ID.json. The report includes timestamps/model and a SHA-256 digest, NOT the prompt/answer or secrets. On POSIX it is created under an owner-only 0700 folder with a 0600 file; on Windows inspect inherited user-profile ACLs. No report is automatically pushed to GitHub.

## Linux/Tencent acceptance in two independently verified layers

If and only if the user independently verifies an actual running Tencent Linux VM and enters it by verified SSH, on the host install a compatible Ollama model and Python 3.11+, and run:

    python3 app/local_ollama_readiness.py
    python3 tools/verify_live_ai_schedule.py --model YOUR_INSTALLED_MODEL --save-redacted-evidence

The CLI refuses to compete with an already running Studio listener. Run during a maintenance window or before enabling the Studio service. This proves one real Ollama due-slot and disposable SQLite readback **on that machine**, not that this is genuinely a Tencent VM or that production systemd survived a reboot.

After starting the separately reviewed private systemd Studio service, run the EXISTING read-only live probe on the same server:

    python3 deploy/verify_private_vps_runtime.py

This second check verifies active systemd, loopback-only port 8765, protected local Mission page and anonymous API rejection. It does NOT call a model. A genuinely complete Tencent 24/7 acceptance still requires: independent Tencent console VM identity/Running state, SSH fingerprint, local resource/capacity checks, actual operator-authenticated scheduled task history, and post-service-restart/post-VM-reboot re-read. No code in this release contacts Tencent or restarts a server.

## Trust and failure boundaries

- Fixed Ollama local endpoint 127.0.0.1:11434 with no redirects, no proxies and no model download. The real CLI calls installed_models() and run_agent() with no user-injectable transport.
- The internal test helper run_disposable_schedule() always returns SYNTHETIC_DISPOSABLE_SCHEDULER_PASSED, never LIVE. Evidence-saving refuses this synthetic status. The dedicated GitHub CI must never claim actual installed model inference.
- The temporary SQLite schedule is max 1 attempted inference and one-day expiry, with a fixed harmless prompt and only calculate / utc_now tools. It does not read or modify the owner's current Studio database or operator safety checkpoint. It runs an isolated worker on a quiet host, then reopens its SQLite state to confirm one completed result and no replay.
- A model may still be processing after a timeout. Never immediately spam retries, especially with limited RAM. The local temporary model checkpoint and files are cleaned up; the user's existing operator checkpoint is NOT silently reset.
- A SQLite reopen is not a full application restart, VM reboot or 24/7 uptime proof. This test is a bounded **on-host smoke test**, not a provider-completion receipt.
- The report stores only redacted metadata and a digest, not the raw model answer. Reports stay private on the owner host and should not be committed with the repository.

## Acceptance state at code merge

The new CLI and synthetic safety tests can be validated by GitHub Actions; real owner Windows inference, real Tencent VPS inference, 24-hour server operation, post-reboot recovery, Google OAuth consent, and downstream n8n task completion remain NOT VERIFIED until the owner independently executes and supplies the corresponding evidence. See docs/PROGRESS.md for exact PR/CI/merge status.
