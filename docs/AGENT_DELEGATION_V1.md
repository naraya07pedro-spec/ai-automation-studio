# Stage #129 · Owner-controlled two-Agent delegation with scoped completion evidence

## What changed and why

The existing Stage #115 durable DAG queue can already run approved read-only plans step by step under owner control. Stage #126 added named Agent tool policies, and Stage #128 added per-project/per-Agent memory references. **Before Stage #129 the durable queue's original intent fingerprint did not cover the new Agent binding or scoped memory references**, so job creation and arm-time identity checks relied on the plan executor only. This stage closes that authorization gap and adds a real, bounded **two-Agent completion handoff** on top of the original queue. It does not invent another scheduler.

A delegated job contains precisely two preexisting, independently approved local Agent plans in serial order. Both plans must be bound to distinct, currently enabled Agent Profiles and independently selected unexpired memory notes in the **same named project**. Each plan uses only its own approved tool allowance; the target never receives a tool, memory card, instruction or provider key from the source. Stage #129 **only transfers durable completion evidence** (a hash of the source's recorded execution receipt) as the DAG dependency barrier. This is not a transfer of raw results or AI-generated text. It is the safe first slice of actual multi-Agent coordination, not full conversational collaboration or automatic specialist spawning.

## Actual backend contract

Use existing, authenticated `POST /api/durable-job-create` with current owner password and CSRF header. Old requests without `delegation` retain original behavior. Optional `delegation` requires exactly:

```json
{
  "job_id": "review_handoff",
  "label": "Research to review",
  "priority": 2,
  "nodes": [
    {"plan_id": "aaaaaaaaaaaaaaaaaaaaaaaa", "depends_on": []},
    {"plan_id": "bbbbbbbbbbbbbbbbbbbbbbbb",
     "depends_on": ["aaaaaaaaaaaaaaaaaaaaaaaa"]}
  ],
  "delegation": {
    "project_id": "engineering_ops",
    "source_agent_id": "research_agent",
    "target_agent_id": "review_agent",
    "handoff_mode": "COMPLETION_PROOF_ONLY"
  },
  "current_password": "<enter into your localhost client only>"
}
```

The two plan IDs above are illustrative; both plans must already exist, be approved, belong to the specified Agents and project, and contain individually selected scoped notes. No AI model may create or approve these jobs. The existing owner-led `/api/durable-job-arm`, `/api/durable-job-run-one`, pause, resume, cancellation, and reconciliation endpoints continue to work unchanged. Each run-one executes **at most one** approved plan step, with a fresh owner password and CSRF. Target execution becomes eligible only once the source plan has completed. A completed source produces `completion_handoff_sha256` in the authenticated job response, while `handoff_payload` remains `NONE`. The underlying plan store is the authoritative source of results. New jobs persist the Agent bindings and selected memory references in a separate `security_sha256` fingerprint for each node; old job records migrate additively with empty security hash, and original intent hashes remain valid.

## Failure modes, revocation and proof

- Source Agent modification/disable or note editing/expiry/deletion stops the target; the entire delegation is reverified on create, arm, resume and each owner run-one.
- Target Agent modification/disable or its note editing/expiry/deletion likewise blocks target dispatch. This also protects *ordinary* new queue nodes, not only delegated jobs.
- Changing a persisted plan's Agent binding or selected notes after the job is created triggers a fingerprint mismatch even if the original goal/steps are unchanged.
- Source receipt evidence is a digest only, not proof of semantic correctness of an AI-generated answer, nor a message body forwarded to a second Agent.
- Crash while executing an actual step still leads to `REVIEW_REQUIRED`, not replay. Owner reconciliation is restricted to an already persisted step and then pauses.
- The owner can inspect job states and metadata but **a target model never sees source output** automatically. Raw, sensitive or adversarial source data cannot cross the Agent boundary through this feature.
- No external connector writes, Gmail, shell, unrestricted MCP, paid AI request, VPS deployment, background job worker or delegated tool privilege escalation is introduced.

The interface to create a delegated job is currently the existing owner HTTP API; a dedicated visual editor for delegation remains deferred until backend-first work is complete. The backend is not proven live on the owner's Windows PC, Tencent VPS, paid OpenAI/Anthropic/Gemini accounts, or real n8n/Gmail integration. Tests use real SQLite/owner-authenticated localhost HTTP, existing read-only tool execution and injected synthetic data. They are not a substitute for owner-device acceptance or actual provider evidence.

## Operational validation

Run `python -m unittest discover -s tests -p 'test_agent_delegation.py' -v`, followed by the existing queue, Agent Profiles, scoped memory, task plan and HTTP regression suites. A dedicated three-version GitHub Actions workflow compiles the modules and runs those checks. The all-repo Python and Chromium workflows must also pass on the exact reviewed PR head before merging. No claim of production readiness follows from CI alone.
