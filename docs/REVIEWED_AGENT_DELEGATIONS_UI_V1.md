# Stage 134 · Reviewed two-Agent Delegations UI

New `/delegations` page uses the EXISTING Stage 129 durable queue and Stage 130 typed cross-Agent handoff. There is no new job engine, no autonomous agent, no provider billing and no new connector privileges.

## Flow
1. Authenticate via the existing owner session, CSRF and local-only host policy.
2. Select two distinct **APPROVED** local plans, each bound to a different Agent with independently selected project/Agent-scoped memory in the same project.
3. Review the exact Agent/project pair, choose job ID/label/priority, enter current owner password and confirm creation. This creates a DRAFT only.
4. Re-enter owner password and separately **arm** the job. Each `run-one` click requires another current password and confirmation, executing **at most one** approved deterministic read-only step. The job and node receipts are persisted by the original queue.
5. If source completed and target still waiting, preview only its final decimal/UTC step into a NEW target note. Owner reauth is required for both the preview and a second digest-bound commit. No arbitrary LLM text or source prompt is transferred.
6. If the receipt is uncertain/CLAIMED_REVIEW_REQUIRED, do not retry. Inspect the saved receipt and exact target-scoped memory, following `docs/TYPED_AGENT_HANDOFF_V1.md`. The existing target plan cannot be retroactively altered; separately propose and approve a NEW plan selecting the transferred note.
7. Read-only plan result inspection and explicit `reconcile` are exposed for already-recorded uncertain states, relying on the server's fail-closed reconciliation rules.

## Surface/security
- New page served at `http://127.0.0.1:8765/delegations`; linked from `/workspace`. Existing `/mission`, `/jobs`, `/handoff` stay intact.
- The HTML login shell may load before authentication, but all plans/jobs/memory/handoff data and mutations require owner authorization. Dynamic text uses `textContent`, no `innerHTML` or browser storage. Owner passwords are cleared after operations and on logout.
- The UI derives source/target Agent IDs and project from real saved plan bindings, not free-text claims. Backend independently revalidates exact plan fingerprints, enabled agents, permissions, memory scope/revisions, queue status and receipt hashes.
- Job and handoff histories may include hashes and metadata; do not mistake completion evidence for model semantic correctness or proof of downstream delivery.

## Automated verification
- Python HTTP route tests assert login shell only and private API 401s without session.
- Browser acceptance uses two independently approved synthetic Agent plans with real SQLite, actual HTTP owner/CSRF/password checks and deterministic calculate/UTC tool steps. It verifies create → arm → source run → typed preview → commit → target still waiting → target run, receipt inspection, target note provenance and desktop/mobile layout render.
- These tests do **not** verify the owner's Windows/Ollama, paid provider accounts, live n8n/Gmail, independent backup media or Tencent VPS availability. Those require private owner-device acceptance.

## Real limitations
- Not parallel or autonomous agent deliberation; the current scheduler/queue remains operator-ticked.
- No automatic cross-Agent memory retrieval, arbitrary result transfer, unlimited context, or external write.
- No auto-retry, and no currency-denominated provider budget enforcement.
- UI intentionally permits only the two-Agent job topology supported by the backend.
