# Stage #125 · Owner acceptance readiness navigator (local-only)

**Date:** 2026-11-08 WIB. One read-only command identifies which of the existing Stage #116, #117, #121, #122, and #120 saved-evidence steps is visible and which existing **inspect** command is appropriate. It never performs a provider action.

## Why this exists

Studio has five separate operator acceptance/history tools plus Stage #124 offline nine-layer verifier. Owners can otherwise mistake an incomplete, failed, capped or previously attempted one-shot read for permission to retry. Stage #125 is a progress navigator, NOT an additional evidence layer or provider success verifier.

## Windows owner procedure

1. Open a reviewed up-to-date checkout on the private Windows computer. Start Studio with Stage #114–#122 strict features enabled and operator login. Do not share password, vault keys, API keys, raw Gmail IDs, recipients, provider responses or backup archives.
2. Find the existing **24-character lowercase hex plan ID** in the private /handoff owner interface.
3. In another PowerShell terminal run:

    ```powershell
    py -3 tools/owner_acceptance_readiness.py --plan-id YOUR_24_LOWERCASE_HEX_PLAN_ID
    ```

4. Enter owner password through the hidden prompt. The script authenticates only to local loopback http://127.0.0.1:8765 (optional --port), performs six authenticated local GETs (capabilities and five history endpoints), then logs out.
5. Read the redacted JSON next_action. It names only a safe **inspect** or review step. Every real remote API GET still needs a separate explicit owner-approved one-shot CLI operation.

## Status interpretation

| Status | Meaning | Next |
| --- | --- | --- |
| CONFIGURATION_BLOCKED | Strict capabilities not enabled | Check original Studio setup; no bypass |
| NEXT_LOCAL_ACCEPTANCE_STEP | Stage not visible in capped history | Use only named existing inspect/preflight |
| CONFLICT_STOP_NO_RETRY | Duplicated, failed, uncertain or inconsistent record | Review original history without retry |
| HISTORY_UNAVAILABLE | Missing/broken local history response | Stop and inspect local state |
| SAVED_CHAIN_PRESENT_RUN_OFFLINE_AUDIT | Five saved success claims visible | Stop Studio and run Stage #124 strict nine-layer audit; then Stage #123 encrypted recovery |
| PREFLIGHT_BLOCKED | Invalid input, unavailable local server or owner login | Fix local prerequisites, no provider request |

**Critical:** History records are recent and capped. NOT_VISIBLE_IN_RECENT_HISTORY does NOT prove no prior request. Do not repeat a provider check based on this result. The individual inspect tools must enforce their own original one-shot ledgers.

A success-shaped report still has saved_record_integrity_audited=false, provider_contacted_by_this_command=false, real_recipient_delivery_verified=false, and production_ready=false. Even Stage #124's saved-record audit does not independently verify recipient delivery or commercial causation.

## Security and tests

Uses Stage #119 authenticated loopback client (fixed endpoints, no arbitrary URLs, proxies or redirects, bounded JSON and guaranteed logout). No email, webhook, model, VPS, backup mutation, provider API GET, file write, or automatic retry. CLI never prints raw plan IDs, contact details, execution IDs or provider responses.

Dedicated tests: tests/test_owner_acceptance_readiness.py; CI: .github/workflows/owner-acceptance-readiness.yml across Python 3.11, 3.12, 3.13. Tests cover missing stages, failures, duplicates, capability drift, malformed history, redaction, only local GETs, logout, and nonzero status for incomplete evidence.

**Still unverified:** Owner Windows run, actual owner n8n/Gmail provider acceptance, physical backup restore, paid OpenAI usage, 24/7 Tencent VPS.
