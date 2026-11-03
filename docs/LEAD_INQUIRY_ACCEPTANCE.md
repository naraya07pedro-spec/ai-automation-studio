# Lead Inquiry Vertical · Acceptance Contract v0.1

**Status:** proposed MVP vertical and testable acceptance criteria; no runtime, connector, or model has been implemented. **Not** an architecture selection.

## Why this slice

The product baseline records a template-first, local-first automation platform with guided setup, user-controlled AI and security, and observable business results (D01–D35). This document translates *one* proposed business workflow into falsifiable behavior before implementation.

## User outcome

A business operator can configure an inquiry intake workflow, process a synthetic inquiry, see a qualification result and draft, authorize or reject a follow-up, and independently inspect whether the intended side effects occurred.

### Explicit non-goals for this first vertical

No automatic mass email, no live CRM writes in initial tests, no cloud account requirement, no marketplace, no real-time collaboration, no always-on behavior while local machine is off, and no claim of universal provider compatibility.

## Proposed journey

1. **Choose template:** Lead Inquiry → Qualification → Follow-up. Install as an inactive draft.
2. **Guided configuration:** request only relevant business fields: channel, qualification criteria, record destination, message policy, AI mode, timezone/business hours, budgets, and operator permissions. Connector-specific credentials are collected via protected fields, never plain logs.
3. **Validate:** verify required configuration, available connector operations, effective permissions, resource/processing restrictions and draft version.
4. **Receive event:** assign a stable inquiry/source event identity. Invalid or unauthenticated external input is rejected or quarantined.
5. **Deduplicate & record:** if the event identity is already processed, do not repeat external effects. Persist enough state to recover processing safely.
6. **Classify:** extract supported fields, retain evidence for classifications, and distinguish uncertain or missing fields from verified values.
7. **Prepare:** create proposed CRM mutation and follow-up draft with explicit target, content, and provenance. Neither is treated as already executed.
8. **Authorize:** evaluate each external write/send against policy and approval binding. A changed recipient, body, target or scoped parameters invalidates an existing approval when material.
9. **Execute safely:** where a live integration is enabled and approved, dispatch with unique action identity, constrained retries and provider-specific reconciliation. Do not assume a timeout means failure.
10. **Verify outcome:** record succeeded, failed, pending, or unknown per action. Expose business outcome plus technical trace without exposing secrets.

## State vs. memory vs. knowledge

- **Execution state:** IDs, transitions, approved action details, checkpoints and outcome evidence needed for integrity/recovery.
- **Agent memory:** optional retention of user preferences or historical context. Disabled by default for the first vertical.
- **Business knowledge:** explicit user-provided qualification rules and source documents, scoped to permitted use. A document is not permission to send or write.
- **Secrets:** segregated from exported templates and routine traces.

## Minimal logical records (not a database schema)

- **Inquiry:** source-event identity, received time, validated source metadata, allowed business fields, privacy label.
- **Workflow revision:** pinned definition version, configuration reference, required capabilities and policies.
- **Proposed action:** action identity, type, target, exact intended parameters, risk, approval requirement.
- **Approval decision:** authorized actor, bound proposed-action version/content, expiry, use limit, outcome.
- **Execution evidence:** stage/status, attempt identity, timestamp, provider outcome where available, reconciliation status, cost estimate and redacted error.
- **Result:** verified business outcome, pending items, and links to redacted trace.

Every record is conceptual; no runtime language, storage engine, or transport protocol selected.

## Acceptance scenarios

| ID | Scenario | Expected observable result |
| --- | --- | --- |
| AC01 | Complete valid guided setup for mock inbox and mock CRM | Draft validates; no production side effects; unresolved fields are disclosed |
| AC02 | Required mapping/credential is missing | Activation blocked with named missing requirement; secret never printed |
| AC03 | Two identical source event IDs arrive | At most one intended external action is authorized/dispatched for the logical event; duplicate is traceable |
| AC04 | Inquiry is missing a qualification fact | Agent returns unknown/needs review, never fabricates a verified fact |
| AC05 | Injection text inside inquiry requests secret exfiltration | Treat text as untrusted data; forbidden tool use blocked and logged without leaked secret |
| AC06 | Draft follow-up lacks approval required by policy | No send; visible awaiting-approval state |
| AC07 | Approval granted for one recipient/body, then recipient or body changes | Old approval rejected; explicit new review required |
| AC08 | Provider times out after receiving a write | Status remains unknown until reconciled; no blind replay |
| AC09 | API rate limits or becomes unavailable | Bounded retry only where safe, then persistent waiting/recovery state; no unbounded cost |
| AC10 | Hard budget exhausted before an AI/API action | New chargeable action blocked; existing state retained with actionable explanation |
| AC11 | Local host goes offline mid-execution | Completed checkpoints remain; external work waits; restart does not promise lost webhook recovery |
| AC12 | User edits workflow while existing inquiry is running | Existing run keeps pinned definition; new draft requires validation before activation |
| AC13 | User cancels workflow while external send is in flight | No new dispatch; previously accepted external action reported as potentially irreversible |
| AC14 | User exports template | No API key, OAuth token, customer data, or private memory in exported content |
| AC15 | Operator lacks permission to write CRM | Proposed write denied even if model recommends it |
| AC16 | Mock test completes all authorized steps | Business dashboard distinguishes draft created from externally delivered, and technical trace includes step outcomes |
| AC17 | Approval expires or is revoked before dispatch | Action denied at execution time, not merely at form review time |
| AC18 | Input contains personal data | Logs omit or redact unnecessary payload, while authorized execution evidence remains usable |

## Testing boundaries

**Phase 1, no external side effects:** static validation, deterministic deduplication, mock CRM and mock message-send responses, injected timeout/rate-limit/error cases, redaction checks, policy denial cases.

**Phase 2, later explicit activation:** connector authentication/capability check and controlled live read-only verification where supported.

**Phase 3, later risk-gated:** live writes/sends to designated test targets, after explicit authorization and recorded compatibility checks. Never use real customers in tests by default.

Evidence labels must distinguish `MOCK_PASSED`, `LIVE_VERIFIED`, `NOT_TESTED`, `BLOCKED`, `FAILED`, and `INCONCLUSIVE`. A mock pass does not certify a provider connection.

## Minimum release gate for first usable vertical

- AC01–AC18 have executable checks or an explicit, justified `NOT_TESTED`/out-of-scope status.
- Required permission and data-processing gates are checked independently of LLM output.
- No real sending/write feature is advertised without live test evidence for its specific connector.
- Every attempt has a distinguishable action identity and outcome state.
- A user can inspect and pause the workflow without making previously completed external actions disappear.
- Product language accurately separates proposed, simulated, submitted, and confirmed actions.

## Open product questions, not architecture decisions

- Which initial intake and CRM destinations have sufficient user demand and official API support?
- What constitutes a qualified lead for a specific template user?
- Which data categories and jurisdictions apply to each deployed business?
- What verified mechanisms can each connector provide for deduplication or outcome reconciliation?

These are resolved through validation and chosen integrations, not guessed in advance.
