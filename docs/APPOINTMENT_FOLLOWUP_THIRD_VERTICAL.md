# Third Executable Mock Vertical: Appointment Follow-up · Slice 31

Date: 2026-11-04. This feature is a deterministic **simulation**, not a live notification scheduler.

## Delivered

`Appointment Follow-up` is the third `EXECUTABLE_MOCK` template in the local gallery. The project now has **3 executable mock workflows and 2 explicitly disabled preview cards**.

The homepage contains a dedicated appointment form (synthetic appointment ID, explicit reference timestamp, appointment timestamp, and mock delivery channel). The browser calls `POST /api/mock-appointment` on the loopback-only Python server and renders status, relative appointment window, draft plan, six-stage redacted timeline, and zero messages sent. No actual email, SMS or calendar event is attempted.

## Contract and constraints

- `prototype/appointment_followup_flow.py` uses deterministic, explicit **offset-aware ISO-8601 timestamps** in `YYYY-MM-DDTHH:MM:SS+07:00` or `Z` form. It does not read the wall clock. Date comparisons normalize timezone offsets through Python aware datetime arithmetic.
- Within 24 hours: `WITHIN_24_HOURS`, `PREPARED_REMINDER_MOCK_ONLY`.
- More than 24 hours ahead and at most 30 days ahead: `UPCOMING_30_DAYS`, `DEFERRED_DRAFT_MOCK_ONLY`.
- Up to seven days in the past: `PAST_APPOINTMENT`, `MANUAL_REVIEW_NO_SEND`. Inputs beyond this temporal window are blocked.
- Only `mock_email` or `mock_sms` channels are accepted. Any real provider identifier, arbitrary free text, missing timezone, out-of-range time, unsafe ID or unknown field is blocked.
- Process-local state detects identical event-ID repeats as duplicates and changed-event-ID payload as conflicts. A bounded 1000-event ceiling blocks further new events. Date validation occurs before IDs are reserved.
- Responses never reveal raw event IDs or timestamps, and always report `external_dispatch_count: 0`, `message_sent: false` and `calendar_event_created: false`. The timeline reports `EXTERNAL_DISPATCH: DISABLED`.

## Running locally

From the repository root run `python app/local_server.py`, open `http://127.0.0.1:8765` on the same machine, and select the Appointment Follow-up card. The default reference time is **2026-11-04 09:00 UTC+7**, and the sample appointment is exactly 24 hours later. Both are intentionally fixed synthetic values, not promises of upcoming real appointments.

## Exclusions and next work

No system timer, background job, delayed notification, real email/SMS provider, external calendar write, verified recipient, per-customer scheduler state, deployment or account credentials. The process-local dedup register resets when the local demo stops.

Next release slices: the mock CRM Handoff fourth executable workflow, then Knowledge & Document Routing fifth executable workflow; validate both through real HTTP calls and documented evidence. Subsequently address cross-workflow user experience, persistent run history, visual browser QA, and a safe public portfolio demonstration.
