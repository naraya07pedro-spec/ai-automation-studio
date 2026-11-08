# Stage #128 · Project- and Agent-scoped owner memory for durable local plans

## Why this changes the product

The preexisting v1 memory cards are 24 global owner notes selected on a per-chat/per-plan basis. Agent Profiles added real per-Agent **tool** policies in #126 but not memory isolation. This stage adds a new owner-only `LocalScopedAgentMemory` store with mandatory **project_id + agent_id + note_id** for each note, explicit read selection, provenance label, revision, immutable generation ID, SHA-256 digest, and bounded time-to-live. It connects those notes to the **real persisted local Ollama plan state machine and durable queue**. This is an independent backend feature, not a visual memory catalog, provider-backed LLM retrieval, or automatic memory extraction.

## Strict boundaries

* All notes are owner-created/edited/deleted; **no AI model can write to memory**. A selected note is treated as untrusted reference text, never as a system rule. Only notes explicitly named by their owner in the same Agent and project scope reach the planner.
* New plans selecting scoped notes must provide an enabled Agent ID, project ID and 1–3 existing memory IDs, and also pass the existing Agent tool-policy check. **Legacy shared memory IDs are rejected for named Agent plans, including previously persisted Agent-bound plans before every step**. Old unbound chat/plans retain their legacy memory behavior.
* A snapshot binds **project, Agent, note, revision, generation, digest, expiration**. Every create/approve/interruption-resume/next-step operation verifies exact unexpired notes and current Agent policy. Edit, deletion, recreation of same name and revision, or expiration refuses further execution; no silent replan or retry. The existing durable queue uses the same verified next-step executor.
* Source provenance is explicitly **OWNER_NOTE** or **OWNER_IMPORTED_UNVERIFIED**, optionally with a short human-supplied label. It is not independent source verification and carries no privilege.
* Retention choices are 7, 30, 90, 365 days; expired notes remain visible to the owner with expired=true and cannot be selected, renewed silently or invoked from an old plan. Owner deletion is explicit. A deleted ID may be created afresh, but has a different generation and cannot revive old references. Max 200 notes total, max 24 per (project, Agent), 600 characters each. This is a bounded **persistent** memory facility, not unlimited context memory or vector search.
* Scoped notes are stored in `~/.ai-automation-studio/scoped-agent-memory.sqlite3`. The database has restrictive OS-user permissions, SQLite secure_delete=ON, synchronous FULL, rollback journaling. **Contents are plaintext at rest**. A logical delete does not erase copies in previous backups, storage snapshots, swap or provider context already sent. For true secure at-rest secrecy, use full-disk encryption and private backup management. Existing stopped-server encrypted offline backup picks up the new SQLite file automatically (subject to its size limits); no off-device restore was run in CI.
* No multi-tenant tenancy, remote sharing or project collaborator permissions. Project/Agent isolation is an **enforced context-selection boundary** inside one owner's local service; the owner can view or delete all their notes.

## Runtime/API

The scoped store is enabled when **both** existing flags `--enable-local-agent --enable-agent-plans --enable-agent-memory` are active. The other v1 unscoped memory paths and older unbound plans keep working.

All calls are localhost-only and require an owner session. Mutations also require CSRF, exact JSON fields and reauthentication with the current owner password, and create redacted `SCOPED_AGENT_MEMORY_CONFIG` audit events without note bodies or passwords.

- `GET /api/agent-scoped-memory?project_id=launch_project&agent_id=research_agent`: list one explicit scope; includes note text only to authenticated owner. It is not a global cross-Agent retrieval.
- `POST /api/agent-scoped-memory-save`: exact JSON fields `project_id`, `agent_id`, `note_id`, `text`, `source_kind`, `source_label`, `retention_days`, `expected_revision`, `current_password`. Initial creation uses revision 0, later edits exact current revision. Disabled Agent cannot receive new notes.
- `POST /api/agent-scoped-memory-delete`: exact `project_id`, `agent_id`, `note_id`, `expected_revision`, `current_password`. Owner can delete notes even after disabling an Agent.
- `POST /api/local-agent-plan-create`: existing `goal` and `model`, plus `agent_id`, `project_id`, `scoped_memory_ids` (1–3). The backend independently selects only exact authorized scope and freezes references in `scoped_memory_refs`; old global `memory_ids` cannot accompany `agent_id`.
- `GET /api/local-agent-capabilities`: boolean `scoped_agent_memory`.

Example illustrative request (NOT a real customer note or credential, and never paste your owner password into a shared document):

```json
{
  "project_id": "launch_project", "agent_id": "research_agent",
  "note_id": "baseline_note", "text": "Check the ROI formula before reporting.",
  "source_kind": "OWNER_NOTE", "source_label": "owner note",
  "retention_days": 90, "expected_revision": 0,
  "current_password": "<type privately into your local client>"
}
```

## Verification and unresolved limits

`tests/test_scoped_agent_memory.py` exercises persistence, scoped reads, current-version selection, expiration, write CAS, delete/recreate ABA protection, enabled Agent policies, read-only step execution, restart/interruption, unauthenticated/CSRF denial, owner password and real authenticated localhost HTTP. It does **not** contact paid AI providers or user Windows, and doesn't prove semantic accuracy of stored notes.

Not done: actual owner Windows/Ollama acceptance, full encrypted at-rest scoped-memory vault, automatic relevance retrieval, vector semantic search, owner-reviewed source citations, general multi-Agent delegation, flexible retention/auto-GC, cross-device sync or offsite deletion propagation. Existing broader roadmap continues backend-first, then UI/Agent-template expansion.
