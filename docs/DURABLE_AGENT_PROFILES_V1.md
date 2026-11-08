# Agent Profiles backend v1

Status: implementation and CI review in progress. This introduces durable single-owner Agent identities that actually restrict existing local read-only task plans, not decorative UI cards.

## Scope

Profiles store a unique agent_id, name, one of six categories (research, engineering, business, creative, productivity, personal), optional description and operating instructions, explicit tool allowlist, enabled flag, revision and timestamps. Maximum 24 profiles, no automatic deletion or eviction. Instructions are owner-authored local plaintext and enter Ollama only as untrusted user-scoped planning guidance; they never grant new tools. No paid model calls, new file permissions, email sends, n8n dispatch, or generic external actions are added.

When Studio is started with --enable-local-agent --enable-agent-plans, profiles are persisted in ~/.ai-automation-studio/agent-profiles.sqlite3. The existing encrypted offline owner backup recursively includes this database after clean shutdown. Profile descriptions/instructions are NOT encrypted at rest, so never enter secrets or private customer data. Disabled profiles are not reactivated in v1; a separate profile ID is required to start again.

## Authenticated API

GET /api/agent-profiles requires owner session and returns current profiles and capacity.
POST /api/agent-profile-create requires the six profile fields (agent_id, name, category, description, instructions, allowed_tools), current_password, owner session, CSRF and same origin.
POST /api/agent-profile-update requires those fields plus expected_revision and current_password; conflicts reject rather than overwrite.
POST /api/agent-profile-disable takes agent_id, expected_revision and current_password; disabling increments revision.
POST /api/local-agent-plan-create may additionally specify agent_id. The response includes exact agent_binding (agent_id plus revision). Existing clients/plans without an agent remain unchanged.
GET /api/local-agent-capabilities includes agent_profiles capability.

The backend checks the profile allowlist on generated proposals and validates its current revision/permissions on plan creation, approval, interruption resume and each explicit execution. Any edit or disabling invalidates old plan bindings and prevents further steps; a new owner-reviewed plan must be prepared. All original feature flags, source grants, manual approval and one-step human action remain required. The existing durable queue also uses the same plan execution gate. Profile changes appear as redacted AGENT_PROFILE_CONFIG security audit events.

## Verification boundary

Tests use temporary SQLite, local HTTP, owner authentication, synthetic planner response and the actual deterministic tool executor. They test negative authorization, tool escalation, revision invalidation and persistence. GitHub Actions are not proof of owner Windows, Ollama, real n8n/Gmail, actual paid model access, or VPS uptime.

Still missing before the whole backend can be called complete: realistic provider routing/cost controls, governed long-term memory, verified real integration outcomes, bounded multi-agent delegation, backup/restore evidence on the owner's environment, resource acceptance and packaged release. Visual Agent Library and additional automation templates are postponed until these priorities are addressed.
