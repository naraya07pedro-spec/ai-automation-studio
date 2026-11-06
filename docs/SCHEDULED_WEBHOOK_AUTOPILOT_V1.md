# Scheduled outbound autopilot V1

Date: 2026-11-06. This connects the real recurring local rule scheduler to the existing opt-in public HTTPS/n8n webhook. It can make automatic real external POST attempts only after a specific owner-approved authorization. It does not schedule arbitrary AI tool calls, scripts, full plugins, CRM edits, WhatsApp messages, or unapproved destinations.

## Activate

1. Configure the existing private AAS_WEBHOOK_URL environment variable to a genuine owner-controlled n8n production HTTPS endpoint, and optionally AAS_WEBHOOK_SIGNING_SECRET. Keep credentials out of GitHub and chat.
2. Start the workbench in offline mode with the explicit --enable-webhook flag. The normal default launcher does not enable external sending.
3. On Mission Control section 04, create and activate a deterministic local workflow. In section 05, configure and activate a recurring schedule from minutes through months.
4. On section 07, select that active schedule and choose a maximum of 1–30 real POST attempts and an expiry of 1–90 days. Click Review exact autopilot policy.
5. Verify the displayed destination hostname, workflow version, exact rule inputs, first eligible scheduled slot, quota and expiration. Check the explicit consent box and re-enter the owner password. Confirm the final automatic-send permission dialog to arm.
6. Leave the server Python process running. View actual policy statuses and outbound receipt history in section 07. The STOP action blocks new claims but cannot cancel a request already sent.
7. Independently check your n8n execution history and configure deduplication by delivery_id at the business-action boundary before allowing n8n to make consequential writes.

The browser can close. The computer or VPS must remain online. Tencent VPS activation has NOT been verified.

## Exact behavior and safety

- Every policy is bound permanently to one already-ACTIVE schedule, its exact config/hash and epoch, the active source workflow version, and one configured external HTTPS destination hash.
- The next due schedule run is evaluated by the existing SQLite rule engine. If a policy is active and all gates match, the worker freezes its actual local receipt into a fixed schema and atomically records a unique (schedule_id, scheduled_for_utc) delivery claim BEFORE any network call.
- Payload includes a stable delivery_id, schedule_id, scheduled_for_utc, workflow_id, workflow_version, decision, reason, and finished_at. All fields are pseudonymous local evidence; not free-form AI text.
- Existing outbound HTTPS sender retains public IPv4 DNS resolution checks, TLS hostname verification, no redirects, port 443, optional HMAC SHA256 signature, and no model-chosen URL.
- HTTP 2xx is REMOTE_ACCEPTED (endpoint accepted, not downstream business completion). Non-2xx, timeout, socket failure, or unknown outcome is OUTCOME_UNCERTAIN and PAUSED_UNCERTAIN. There is no automatic retry.
- If the app crashes mid-request, startup quarantines any IN_FLIGHT delivery as OUTCOME_UNCERTAIN and stops its policy. A stopped, exhausted, expired, or quarantined policy is never silently reactivated; a new schedule and new explicit owner grant are needed.
- Pausing/resuming a schedule, changing its control epoch, changing the underlying workflow version, or changing the configured destination automatically ends policy eligibility.
- Limits: 24 policies lifetime per installation, aligned with the current 24-schedule storage cap; each grant authorizes 1–30 outbound attempts and expires in 1–90 days. A worker examines at most 3 active policies per tick; only the latest eligible completed slot is considered to avoid backlog floods.
- This is not distributed exactly-once external delivery or an encrypted credential vault. Process environment is visible to the service account/admin. Provider billing and n8n execution outcomes are external to this software.

## Verification scope

CI tests use an injected HTTPS sender and a disposable owner-authenticated localhost server, so no actual customer's n8n instance is touched. These establish local scheduling, approval enforcement, cap/expiry, stop behavior, uncertain-result quarantine, crash recovery, and no blind replay. They do NOT establish Tencent VPS Running, real n8n acceptance, public internet deployment, or availability of all connector plugins.

See docs/SCHEDULER_REAL_V1.md and docs/OUTBOUND_CONNECTOR_REAL_V1.md for the two underlying components.
