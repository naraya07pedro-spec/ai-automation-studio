# Stage #130 · Owner-reviewed typed result transfer across two Agent scopes

## Product capability

Stage #129 could coordinate two distinct, approved Agent plans, but transferred only a completion digest. Stage #130 adds **actual data transfer with owner review** into a NEW isolated note for the destination Agent. This uses the original real local read-only plan executor, source completion evidence and Stage #128 durable scoped memory. It does not create a second orchestration engine or send an AI model output to another model automatically.

**Supported typed outputs:** strict bounded decimal output from the deterministic \`calculate\` tool and UTC timestamp output from \`utc_now\`. NO LLM-generated free text, document excerpts, email, GitHub/CSV data, secrets, markdown, code, external provider results or arbitrary tool values can cross this boundary. Both agents must be in the same project with existing approved separate plans and valid Agent/profile/memory permissions, the source must be COMPLETED with matching durable receipt, and the target plan must still be waiting, untouched and approved. The source+target plan fingerprints, runtime policy, source step result and registered source completion digest must match.

**The critical difference from prior stage:** the permitted typed value itself is copied to a **new** \`OWNER_IMPORTED_UNVERIFIED\` target-scoped note, only after a second explicit owner action. It does not alter, automatically reapprove or feed the existing target plan. To use the copied value, the owner explicitly chooses that note for a **new destination Agent plan**, reviews its proposed steps and approves them. No autonomous orchestration, prompt transfer or model provider calls occur in this transfer.

## API and review contract

Enabled only alongside the existing local Agent, plan and scoped memory capabilities; owner session, same origin and CSRF are required for any mutation. Each preview and commit also requires the CURRENT owner password and writes a redacted \`AGENT_TYPED_HANDOFF\` audit event.

\`POST /api/agent-typed-handoff-preview\` accepts:

\`\`\`json
{
  "job_id": "typed_job",
  "source_step_index": 0,
  "target_note_id": "imported_result",
  "retention_days": 30,
  "current_password": "<private owner entry>"
}
\`\`\`

\`job_id\` must refer to an existing Stage #129 **delegated two-Agent queue**. The source must have completed and the recorded output step must be its **final step**, so it matches the actual SHA-256 receipt the queue persisted. The target must be waiting. The selected target note name must not already exist. Preview returns \`typed_result\` for the owner to review, source receipt SHA-256, project and target Agent, plus \`preview_id\` and \`payload_sha256\`; it causes NO target memory mutation.

After reviewing, \`POST /api/agent-typed-handoff-commit\` accepts exactly the preview ID, matching digest and current owner password. Preview expires after 10 minutes and stale Agent policies, result evidence, source note, target scope, job state or matching digest invalidate it. The committed destination note has source kind \`OWNER_IMPORTED_UNVERIFIED\`, source label \`Typed handoff <job_id>\`, bounded typed text and selected 7/30/90/365-day retention. Only a **new note** can be created, never overwriting one.

The authenticated \`GET /api/agent-typed-handoff-receipt?preview_id=...\` returns the durable metadata receipt (no raw typed value). \`GET /api/local-agent-capabilities\` now includes \`typed_agent_handoff\`. All API routes are localhost-only and existing general owner authentication remains mandatory.

## Crash and privacy model

The handoff ledger is private \`~/.ai-automation-studio/agent-typed-handoffs.sqlite3\`. Preview stores a SHA-256 digest and request selection metadata, but **not the typed value**. The selected text is recomputed from the persisted source plan on commit and revalidated. Commit first writes a durable \`CLAIMED_REVIEW_REQUIRED\` marker BEFORE performing the local target memory write. Successful note storage is followed by a \`COMMITTED\` receipt. The two SQLite databases are not guaranteed atomic across a power failure. If anything fails after the claim, state remains \`CLAIMED_REVIEW_REQUIRED\`; the operator must inspect both the target scoped note and handoff receipt manually. Neither the API nor a restart automatically retries the transfer. This avoids duplicate silent writes at the cost of conservative manual recovery. No automatic reconciliation or owner override is included in this initial release.

Target memory is **plaintext on disk** and previously copied values may survive in offline backups, swap or model-context history. Deletion from target does not retroactively remove external copies. The typed value should be treated as unverified source-derived data, not privileged instructions. A later source permission revocation does not automatically delete an already approved and copied target note: the owner must revoke that note separately. No external provider calls or external writes are performed.

## Verification and limits

\`tests/test_typed_agent_handoff.py\` covers real SQLite, real queue step execution, owner-authenticated localhost HTTP, preview/commit/replay denial, separate newly approved target plan, modified Agent/memory policy, corrupted result/receipt, timeout, target-note overwrite denial, source tool restrictions, and an injected crash after the target note reached storage. These tests use deterministic fake data and cannot establish owner Windows/Ollama performance, live paid API charges, n8n/Gmail delivery, VPS persistence or production-readiness. UI redesign and new template libraries are intentionally deferred until backend priorities are cleared.
