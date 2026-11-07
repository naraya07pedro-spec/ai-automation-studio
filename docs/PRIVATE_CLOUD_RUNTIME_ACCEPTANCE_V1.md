# Stage #109 · Read-only real-host cloud AI receipt acceptance · 7 Nov 2026

## Goal, not an uptime claim

Stage #108 shipped cloud-only OpenAI scheduling, but did not connect to Tencent
Cloud, issue a real paid request or prove service persistence in production.

This stage adds `deploy/verify_private_cloud_ai_runtime.py`: a **read-only,
non-root on-host verifier** that correlates the *current* systemd private
workbench process, private HTTP/auth check, declared systemd API-key credential,
and a single-attempt completed OpenAI-labeled AI task with recorded token usage
in the owner's existing SQLite database.

**This is not a provider-authenticated receipt.** A local DB row can be forged
or produced by a test runner. The verifier explicitly leaves
`provider_account_and_billing_verified=false` even on its highest successful
status. It never marks Tencent identity, reboot persistence or 24-hour
availability verified. The operator must reconcile the actual provider usage
independently before claiming live model success.

## Safe operator sequence on the *actual* Tencent Linux VPS

0. Independently confirm the instance is RUNNING, Linux/systemd is available,
   and SSH host-key fingerprint matches the Tencent console. Do not post any
   SSH keys, app passwords, API keys or instance-private identifiers into GitHub
   issues, reports or chats. Use the **non-root service account** for commands.
1. Check out the reviewed and merged code under the service account. Follow
   `docs/CLOUD_ONLY_PRIVATE_AI_V1.md` for owner provisioning, API-key
   provisioning via hidden root-only prompt, and a deliberate cloud-only
   systemd installation. Existing service units must not be overwritten blindly;
   back up and manually review existing units before a planned change.
2. Verify private HTTP and effective systemd process from the same nonroot
   service identity:

       python3 deploy/verify_private_vps_runtime.py
       python3 deploy/verify_private_cloud_ai_runtime.py

   The cloud-specific verifier returns `NOT_VERIFIED` and exit code 2 until
   all checks and a matching finished task exist. That is expected on a freshly
   initialized system. It does not create tasks or send provider requests.
3. Establish the existing secure SSH tunnel from Windows using
   `CONNECT_PRIVATE_VPS.cmd`, after closing any local Studio occupying port
   8765. Open `http://127.0.0.1:8765/mission` and log in with the owner
   password. Never expose port 8765 to the internet.
4. In section 12, create exactly **one** harmless, short, nonsensitive OpenAI
   Responses text task with `max_runs=1`, `expires_days=1`, one-minute
   cadence, an actual API-enabled model ID, explicit paid-API consent and
   owner authorization. The API bill is separate from ChatGPT Plus. No model
   tools, external sends, Gmail, n8n or shell actions are requested.
5. Wait for the due slot through Mission Control, inspect the completed
   response and token values, then rerun:

       python3 deploy/verify_private_cloud_ai_runtime.py

   It may now report `CLOUD_RUNTIME_RECEIPT_OBSERVED_REVIEW_REQUIRED` with
   an anonymized task fingerprint and input/output token counts. It **must
   not** expose the model answer, prompt, full task ID, private key or unit
   contents. A pause, auth/quota error or uncertain result is **not** proof of
   a completed call. Do not retry a possibly billable timeout without first
   reviewing provider usage.
6. In the owner's **actual OpenAI API account**, reconcile request, usage,
   timing and cost independently. Do not infer a dollar price from tokens
   alone. Record only a redacted private acceptance report (not credentials).
   Reboot/schedule persistence must be evaluated separately with the existing
   `verify_private_vps_reboot.py` and
   `verify_post_reboot_rule_run.py` guidance. The latter only proves
   deterministic local rule-worker execution, **not AI after reboot**.
7. Prove 24-hour operation through real measurements taken over that period;
   a single successful snapshot cannot establish 24/7 availability.

## Meaning of checks

- `private_vps_runtime_snapshot`: existing read-only verifier saw active
  systemd, localhost-only port 8765, login page security headers and rejection
  of anonymous protected API access.
- `nonroot_service_identity`: current `systemd MainPID` belongs to the
  nonroot user running this verifier; avoids trusting a different instance.
- `live_cloud_only_server_process`: live `/proc/<pid>/cmdline` is the
  Python Studio server with `--enable-scheduled-openai` and without Ollama,
  outbound-webhook or managed-autopilot flags.
- `root_owned_credential_declared_in_unit`: systemd unit declares the
  existing fixed `LoadCredential` source; this script does not read secret
  bytes or assert provider account validity.
- `bounded_remote_receipt_in_owner_sqlite`: read-only SQLite query found
  an OpenAI-labeled completed task with exactly one authorized attempt,
  nonempty answer, no model tool calls, and bounded positive input/output
  token usage. It does not print the answer or task ID.

**Evidence ceiling:** `CLOUD_RUNTIME_RECEIPT_OBSERVED_REVIEW_REQUIRED`
means a nonroot Linux snapshot plus local receipt shape passed. It does *not*
mean `LIVE_OPENAI_MODEL_VERIFIED` or `ONLINE_24_7_VERIFIED`.

## CI / progress record

- Branch: `feat/stage-109-cloud-runtime-acceptance`.
- Added: `deploy/verify_private_cloud_ai_runtime.py`, its disposable
  negative-path tests and a Python 3.11/3.12/3.13 GitHub Actions workflow.
- CI mocks/fixtures must never report paid provider success or Tencent
  availability; they validate fail-closed behavior and secret redaction.
- Genuine on-host execution: **NOT YET OBSERVED**.
- OpenAI provider-side billable usage: **NOT YET OBSERVED**.
- Tencent identity / reboot / 24-hour uptime: **NOT YET OBSERVED**.

Maintain these limits in `docs/PROGRESS.md` after merge and CI review.
