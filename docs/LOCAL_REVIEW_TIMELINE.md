# Mock execution timeline and local draft review · Slice 28

Date: 2026-11-04. Development-only loopback application. No real sending, CRM writes, operator identity verification, external authorization, or data persistence.

## User-visible journey

1. Run a synthetic Lead Inquiry through the existing validation, deduplication and qualification flow.
2. View seven server-derived stages: INTAKE, CONFIG_VALIDATE, DEDUP, QUALIFICATION, DRAFT, REVIEW, EXTERNAL_DISPATCH. External dispatch remains DISABLED.
3. Only a fresh, fully qualified first-seen mock inquiry creates a **fixed canned follow-up draft** with recipient NOT_SET and delivery NEVER_SENT.
4. User may press **Acknowledge review** or **Reject draft**. This consumes a one-time random token and updates only the temporary local review state. It is **not** approval to dispatch and cannot mark an event completed.
5. Duplicate/conflicting inquiries cannot create new review tokens. There is no draft for blocked events.

## Implemented interfaces

- `prototype/lead_inquiry_demo_review.py` builds redacted timeline and an in-memory, bounded 100-pending-token store. Tokens are scoped to this local server process and never sent back in review decision responses.
- `app/local_server.py` returns `timeline` and `review_offer` alongside the existing response from `/api/mock-run`. `POST /api/mock-review` accepts only `review_token` and `decision` (`ACKNOWLEDGE` or `REJECT`) with existing size/content-type gates.
- Cross-origin and unexpected Host headers on mutation routes are rejected as a basic local dev boundary. This is not comprehensive web authentication or CSRF hardening.
- `web/index.html` renders stage status and fixed draft with DOM `textContent` and disables review buttons after a decision.

## Limits and next steps

- Acknowledgement/rejection only records a local UI demonstration; no real operator identity, persisted authorization or signed review.
- Tokens and dedup state vanish on server restart. There is no durable execution history or rollback.
- Fixed draft content does not reflect the inbound inquiry and is **not** AI-generated. The UI does not pretend it has sent an email.
- This implementation has no public deployment, live integrations, local user login or source authenticity checks. Keep listening on 127.0.0.1 only.
- Next: add a multi-template gallery with honest preview labels, and bounded guided setup. No connector activation or sending until their own verification gates exist.
