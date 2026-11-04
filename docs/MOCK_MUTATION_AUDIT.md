# Mock state mutation inventory · Slice 16 · 2026-11-04

Scope: review of the repository's `prototype/` Python modules at the start of slice 16.
This is a source-level inventory, not a penetration test or a proof that every call
site, dynamic import, or future integration has been inspected.

| Component | Mutating surface | What it changes | Trust boundary / finding |
| --- | --- | --- | --- |
| `lead_inquiry_durable_state.DurableMockEventStore` | `reserve`, `mark_mock_completed` | `mock_events` PENDING/COMPLETED | Direct mock completion is still callable without auth; do **not** use for real provider outcomes |
| `durable_mock_state.DurableMockEventStore` | `record` | Separate mock decision table | Overlapping legacy experiment; no production access controls, consolidation pending |
| `lead_inquiry_pending_review.MockPendingReview` | `acknowledge` | `mock_pending_reviews` | Caller-supplied reviewer reference; separate acknowledgement only, not event completion |
| `lead_inquiry_review_transitions.MockReviewTransitions` | `acknowledge_hold` | **None** | Deliberately disabled by slice 14, returns `LEGACY_PATH_DISABLED` |
| `lead_inquiry_single_use_grants.MockSingleUseReviewGrants` | `issue`, `consume_hold` | `mock_review_grants`, `mock_review_transitions` | Single-use transaction, but constructor authority/clock can be injected by any code that can instantiate it |
| `lead_inquiry_flow.MockInquiryFlow` | `process` | In-memory dedup or durable mock event state | Mock-only, separate from review hold; still contains mock-completion mutation |
| `lead_inquiry_dedup.InMemoryDeduplicator` | `register` | In-memory event fingerprints | No durable coordination |
| `lead_inquiry_validation`, `lead_inquiry_qualification`, `lead_inquiry_reconciliation_preflight` | None in their exported validation/classification functions | No persistence | Preflight verifier is injected and cannot attest real identity |
| **New** `lead_inquiry_review_orchestrator.MockReviewOrchestrator` | `request_hold_grant`, `hold_with_grant` | Via grant coordinator | Recommended narrow mock facade; excludes request-level authority and clock, but is **not** an enforced access-control boundary |

## What is supported now

The facade provides only bounded pending inspection, review inspection, mock grant issuance,
and atomic review hold consumption. It does not expose a direct mock completion method or
accept verifier, time, expiry or a raw preflight success string in its public request methods.
It does not make live network calls.

## Gaps that remain high priority

1. **Direct class access bypasses the facade.** Python modules and SQLite files are accessible
   to other code in the same process. The facade cannot guarantee that all state mutations go
   through it; there is no process isolation, app-level module access policy, secure DB credential
   boundary or trusted deployed composition root.
2. **Identity is simulated.** A caller constructing the facade can inject an always-allow
   authority and arbitrary clock. Evidence is still only digest-format checked.
3. **Other mutators remain.** `mark_mock_completed`, `record`, and the separate
   `MockPendingReview.acknowledge` continue to exist for prototype compatibility.
4. **Data trust is not proven.** No auth session, independent provider receipt, signed evidence,
   real-world recovery, disk encryption, backup/restore or retention governance is implemented.
5. **No architecture lock.** SQLite and Python are tools for a constrained experiment, not an
   enterprise production stack decision.

## Recommended next smallest slice

Quarantine or restrict **direct mock completion** and overlapping legacy mutators in
small regression-safe changes. Then design the real authority/identity trust root separately.
No real external effects until both are demonstrably enforced.
