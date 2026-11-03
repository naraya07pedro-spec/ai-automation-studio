# Product Baseline v0.1

Status: **product decisions recorded, not implemented**. Source: 35 decisions agreed in the AI Automation Studio product-discovery conversation up to 2026-11-03. This document is a product baseline, **not** a technical architecture or a claim of delivered features.

## Product promise

AI Automation Studio is a **template-first business AI-agent and automation platform**. A beginner chooses a template, completes a context-specific guided setup (including verified integration instructions), tests it, activates permitted automation, and monitors verified outcomes. An advanced user can inspect and edit workflows. Local-first ownership is a product constraint, not a promise that cloud integrations can run without internet.

## Recorded decisions

| ID | Product decision |
| --- | --- |
| D01 | User-created private templates |
| D02 | Guided forms for beginners and a visual workflow editor for advanced users |
| D03 | Managed AI plus bring-your-own-key, with supported local and custom models |
| D04 | Policy-based autopilot; sensitive actions require appropriate approval |
| D05 | Event triggers, chat commands, and ongoing work |
| D06 | Broad future segments; initial UX favors nontechnical businesses |
| D07 | Local-first hybrid: user-controlled local, self-hosted, optionally managed hosting |
| D08 | Desktop and local-web/self-hosted distribution |
| D09 | Risk-aware hybrid updates with compatibility and recovery checks |
| D10 | Adaptive installation: beginner defaults and advanced choices |
| D11 | Adaptive storage; separate operational state from optional AI memory |
| D12 | Adaptive backup, recovery, export/import, and portability |
| D13 | Fault-tolerant autopilot, safe retries, and uncertain-outcome handling |
| D14 | Cost and resource governance, including hard budgets |
| D15 | Adaptive task scheduling with deadlines, dependencies, and fairness |
| D16 | Adaptive agent permissions with independent enforcement |
| D17 | AI-assisted template creation with validated setup forms and tests |
| D18 | Private, team, and exportable templates; marketplace optional later |
| D19 | Optional workspace collaboration; personal local mode without cloud account |
| D20 | Business-friendly operations dashboard plus technical observability |
| D21 | Risk-based agent testing and activation gates |
| D22 | Draft/publish and immutable active workflow versions, safe deployments |
| D23 | Official and custom connector ecosystem: APIs, webhooks, supported MCP/tools |
| D24 | Single-agent default; controlled multi-agent orchestration when justified |
| D25 | Human-in-the-loop approvals bound to concrete actions |
| D26 | Governed knowledge/context sources distinct from memory and runtime state |
| D27 | Policy-first adaptive model routing with compatibility-checked fallback |
| D28 | Offline-capable local work, persistent queues, and controlled synchronization |
| D29 | Adaptive, privacy-conscious notifications with optional external channels |
| D30 | Data lifecycle, retention, export, deletion scope, and compliance controls |
| D31 | Incident detection, containment, emergency stop, and controlled recovery |
| D32 | Evidence-driven evaluation and controlled improvement of agents |
| D33 | Account-free local core; separate workspace identity and optional paid services |
| D34 | Adaptive resource isolation and fair scheduling |
| D35 | Provider dependency resilience and controlled migration/fallback |

## Non-negotiable product invariants

1. **No silent data movement:** external AI, hosting, sync, and notifications respect explicit processing boundaries.
2. **No capability or permission fabrication:** proposed actions must match available, verified connector capabilities and effective permissions.
3. **No unverified success:** distinguish succeeded, failed, pending, and unknown outcomes.
4. **No blind replays:** an uncertain external action cannot be safely retried without reconciliation or an explicitly authorized policy.
5. **No destructive silent updates:** published workflow versions and user data have controlled migration and rollback limitations.
6. **No hidden cost overruns:** hard platform budgets govern new actions; BYOK provider invoices may differ from platform estimates.
7. **No forced cloud identity for local core:** local data ownership survives loss of optional managed-service entitlement.
8. **No unbounded autonomy:** delegation, retries, concurrency, and approvals have explicit constraints.
9. **No misleading offline promise:** local execution stops when its host is off; inbound events require a durable external sender/relay to survive downtime.
10. **No false compliance guarantee:** deletion and security claims must state exactly what was verified.

## Deliberately deferred

Architecture, framework, programming language, persistence engine, desktop packaging, hosting topology, external model/provider shortlist, final licensing, pricing, and public marketplace are **not yet selected**.

## Thin first vertical (proposal, not locked)

Demonstrate one complete **lead inquiry → validate/qualify → record/update → draft follow-up → approval or policy-authorized send → verify outcome** journey. Build only the connectors and controls this vertical needs. Prioritize a functioning, testable slice over a wide catalog or marketplace.

## Acceptance criteria for this documentation slice

- D01–D35 recorded without treating proposals as implementation.
- Runtime state, optional AI memory, and business knowledge are explicitly distinct.
- Local-first and external-provider limitations are explicit.
- MVP vertical marked as a proposal, not a final commitment.
- No code, secrets, dependencies, or architecture selected by this change.
