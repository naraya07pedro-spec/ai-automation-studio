# Real recurring scheduler V1 · 2026-11-06

## What is now implemented

Mission Control contains **05 · Recurring task scheduler** with real backend APIs and a server-owned background worker. After signing in, create an **ACTIVE local rule workflow** under section 04, then schedule the existing deterministic qualification rules with a fixed, pseudonymous category/budget/timeline payload. The worker evaluates this task automatically, saves its result and lets the owner inspect execution receipts. **The browser does not need to remain open. The Python application process does need to remain running.**

This is not a calendar reminder mock, and it never sends an email, WhatsApp message, webhook, model call or CRM update. It cannot schedule arbitrary AI plans, scripts, MCP tools or external reads/writes. Those need separately authorized connectors, task-specific capabilities and recovery-safe dispatch designs.

## Cadences available

| Mode | Configuration | Example |
| --- | --- | --- |
| Minutes | Every N minutes (1–1440) | Every 1, 5, 15, 30 minutes |
| Hours | Every N hours (1–168) | Every 1, 6, 24 hours |
| Daily | Local wall clock `HH:MM` | 08:30 daily in Asia/Jakarta |
| Weekly | Weekday (Monday=0) and `HH:MM` | Monday 08:30 WIB |
| Monthly | Day 1–31 and `HH:MM` | 31st at 08:30 WIB; clamped to last day in shorter months |

Supported zones in this first execution engine are **Asia/Jakarta (fixed UTC+7, no DST)** and **UTC**. Interval schedules start **N minutes/hours after creation** and advance by interval after each server wake-up. Calendar schedules are computed for the next future local wall-clock occurrence and stored as UTC. V1 does not support arbitrary cron syntax, holiday exceptions, business-day logic or all global DST zones.

## Engine, persistence and restart semantics

- The existing Python HTTP server creates a `LocalRecurringScheduler` against `~/.ai-automation-studio/recurring-schedules.sqlite3` and starts one worker thread as part of server startup. It checks due work approximately every **5 seconds**.
- Only owner-authenticated, same-origin, CSRF-protected requests can create/pause/resume schedules and read the schedule ledger. Supported API endpoints: `GET /api/local-schedules`, `GET /api/local-schedule-history?schedule_id=<24 hex>`, `POST /api/local-schedule-create`, `POST /api/local-schedule-pause`, `POST /api/local-schedule-resume`.
- Schedules persist across application restarts. A durable, unique `(schedule_id,slot_utc)` ledger records each claimed run, and the worker freezes the active rule version and inputs before evaluation. If a process dies while one purely local evaluation is `IN_FLIGHT`, the next start **re-evaluates the same frozen snapshot**, then completes the receipt once. **Never extend this recovery assumption to external dispatch.**
- After shutdown or long downtime, **at most one overdue occurrence per schedule** is processed on restart, and earlier missed occurrences are skipped. This avoids burst execution. V1 is neither an exactly-once distributed task system nor a calendar catch-up engine.
- Pausing a schedule prevents future claims; resuming explicitly calculates the next future due time. If its source workflow becomes inactive, a due run is recorded as `BLOCKED / WORKFLOW_NOT_ACTIVE` and the schedule auto-pauses.
- A schedule has immutable cadence and input payload in V1. To change it, create a new schedule and pause the previous one. Limit: **24 schedules**, **8 claims per worker tick**; the newest **2,000 receipts per schedule** are retained while lifetime completed/blocked counters remain.
- The exact deterministic rule function is the one shared with the local workflow core; scheduled receipts are stored in the **scheduler ledger**, not silently inserted into the 500-event manual local-run ledger. There are no real customer records or destinations.

## Run it now

On a Windows PC with Python 3.11+ and the updated repository:

1. Start `START_AI_STUDIO.cmd --mode offline`, or double-click `START_AI_STUDIO.cmd` for the full panel set (which still needs an installed Ollama model for actual AI inference).
2. Sign in at `http://127.0.0.1:8765/mission`.
3. Under **04 · Local automation**, create a draft using nonpersonal identifiers, then **Activate draft**.
4. Under **05 · Recurring task scheduler**, select the active workflow, choose every **1 minute**, select a fixed service category, budget tier and timeline, and click **Activate recurring schedule**.
5. Leave the Python app console running. After the next due time, click **Refresh**, **View history**, and verify the real `COMPLETED` decision or any `BLOCKED` reason. Use **Pause/Resume** as needed.

To keep it running when the PC is off, install the **existing private systemd release** on an actual live Linux VPS and access it through the SSH tunnel. The operator's Tencent instance is **not yet verified**. A successfully merged code change alone does not make it a 24/7 online service.

## Explicitly outstanding

No generic plugin manager, OAuth credential vault, MCP marketplace, connector-wide action permissions, external CRM/email/WhatsApp sending, hosted uptime monitor, cron-expression editor, public HTTPS deployment, real Tencent instance or on-device Ollama result. These require additional implementation; do not present this local scheduler as a universal personal agent.

Targeted Python checks exercise daily/weekly/month-end calculation, automatic local execution, persistence, pause/resume, crash recovery, schedule input rejection and the authenticated HTTP schedule path. Existing GitHub CI will also run its normal regression gates. Actual user-machine Windows/VPS activation is a separate live acceptance gate.
