# Personal AI Harness / AI Automation Studio · Execution roadmap (living)

Last reviewed: **7 November 2026 (WIB)**. This record is a decision-and-evidence ledger, not a claim that the overall product is finished. Owner priority: make a usable, trustworthy personal AI workbench **without depending on Tencent VPS first**. No product-level fake percentage-complete score.

## Objective and boundaries

**AI Automation Studio** is a local-first, template-oriented business-automation workbench. **Personal AI Harness** aspires to a broader owner-controlled assistant across provider models, context, authorized tools and long-running jobs. The former contains building blocks for the latter but does not yet implement the entire original D01–D35 vision in `docs/PRODUCT_BASELINE.md`.

Critical distinctions:
- **Merged**: code exists on `main`.
- **CI verified**: disposable/local acceptance and regression checks passed on tested commit.
- **Live verified**: owner actually ran the same feature on their own environment/provider account and checked the resulting outcome.
- **Production ready**: repeatable installation, recovery, threat boundaries, monitoring, load/operational checks and real app/provider evidence passed.
- No claim automatically upgrades one category to the next.

## Proven engineering increments

| Stage | Functional implementation now on main | Evidence | Live owner-provider status |
| --- | --- | --- | --- |
| #108 | Private cloud-only scheduled OpenAI Responses without Ollama | Merged; deterministic CI | Real provider request and 24-hour uptime unverified |
| #109 | Read-only on-host runtime acceptance utility | Merged; isolated CI | Actual VPS acceptance unverified |
| #110 | Guided first-time VPS activation utility (optional, deliberately not run) | Merged; isolated CI | Actual Tencent access/installation unverified |
| #111 | Encrypted offline local backup, verify, disposable-only restore | Merged; Linux/Windows synthetic CI | Backup/restore of actual owner state unverified |
| #112 | Owner-confirmed immediate paid OpenAI text request, durable ID and receipts | Merged; 15/15 CI, 1,220 full suite tests per Python runtime | Real billable account/model response unverified |
| **#113** | Cloud AI proposes a 1–3 step local read-only plan, owner separately approves and executes actual deterministic calculator/clock tools; safe no-charge receipt recovery | **PR #113 merged as `314c180ff2ad8ef077014daacedcde1035a5a67d`; 15/15 CI; 1,228 full-suite tests on Python 3.11/3.12/3.13 (74 optional skips each); 8 focused tests per version** | **Actual paid model answer and owner-PC execution unverified** |
| **#114** | Owner-selected completed-plan evidence bound to an existing immutable local workflow receipt and encrypted managed HTTPS connector, separately approved single-send with durable no-retry/uncertain ledger, private `/handoff` UI | **[PR #114](https://github.com/naraya07pedro-spec/ai-automation-studio/pull/114) merged as `636194df55f86e100d5551e4707821f91b686237`; exact tested head `622fd1a1511e7c2df21361175b9adc8cbb215558`; 17/17 CI success, 1,237 Python suite tests per 3 runtimes (83 skips each), Chromium 58/58** | Real owner PC, live n8n/HTTP downstream final outcome and provider usage unverified |

The #113 improvement is **a complete engineering slice from a fake provider result to real local tools**, not live proof of the paid provider and not a general multi-tool autonomous agent. It does not obtain permission from model-supplied text, does not send email or mutate live CRM, and does not invoke tools without owner approval.

### Stage #114 engineering advance (merged, CI verified)

An owner-approved **plan-provenance → deterministic-workflow receipt → encrypted managed connector** handoff has been implemented and merged to `main`. It adds a durable local evidence binding, independently password-approved HTTPS one-shot send, no-retry uncertainty state, private UI, and new test coverage. It **does not** translate agent results into a business event or independently prove downstream effects. CI passed on the exact PR head, but real owner-PC/provider and downstream acceptance are still pending. Full scope: `docs/AGENT_CONNECTOR_HANDOFF_V1.md`.

## Next implementation work, in priority order

### P0 · Actual owner-PC acceptance and continuity (next operator gate)

- Confirm reviewed checkout, local operator login, local owner-state permissions and private loopback binding on actual Windows PC.
- Run an actual **read-only** local task; restart; verify existing plan, ledger, audit and owner state survive.
- With separately provisioned paid API key and explicit owner consent, run *one benign, low-cost* cloud model prompt/proposal. Reconcile actual provider usage/billing independently, test receipt recovery and deliberate local tool execution.
- Run encrypted backup, integrity verification and disposable restore to an owner-controlled private directory and check off-device copy if available.
- **Blocked on real operator PC / provider authorization**, not on more synthetic GitHub CI. No raw API keys, passwords, recovery codes or owner database bytes should be uploaded to GitHub/chat.

### P1 · Actual approved connector task with final outcome verification

Implement one authorized end-to-end workflow: input → validated intent/plan → scoped exact connector permission → preview → independent owner approval → dispatch → receipt/reconciliation → verified provider outcome, including uncertain status and no blind replay. Reuse existing n8n/Gmail/connector components, but do not conflate webhook HTTP 2xx with completed external business effects. Do not claim universal tool access.

### P2 · Genuine multi-provider model routing

Define verified provider capability contracts and consent-based budget/account policy before adding models. Build deterministic routing, explicit fallback and provenance of the chosen provider, with no silent premium calls or cross-provider data transfer. API interoperability and ChatGPT/Claude/Gemini subscription access are not interchangeable. Test deliberately failed, unsupported, throttled and ambiguous billable requests.

### P3 · Governed long-term context and knowledge

Separate durable user preferences, project facts, retrieval sources, task state and execution receipts. Make source provenance, owner-controlled retention and deletion, consent to external processing, prompt-injection boundaries and encryption/backup portability explicit. Do not silently infer unlimited memory from a handful of manually selected local cards.

### P4 · Durable work orchestration across interruptions

Add explicit job dependencies, priority/fairness, bounded retries with reconciliation, pause/stop, restarts and resumable operator approval state, while preserving strict idempotency and non-replay of uncertain external writes. Local power-off means local execution cannot continue: persisted queues preserve work, they do not supply magic availability.

### P5 · Usable software and release engineering

Review visual workflow editor and guided template onboarding, installer/desktop distribution, permissions UX, disaster recovery, accessibility, OS isolation, actual resource budgets, support diagnostics, upgrades, and real user testing. Optional hosted/VPS deployment is not a prerequisite for the offline/local core, but is separately necessary for genuine unattended 24/7 cloud-hosted work.

## Hard acceptance gates before declaring the entire product complete

1. Actual owner environment passed: installation, login, startup, restart and private storage checks.
2. At least one real remote model call paid with explicit consent; actual response and provider-side usage reconciled.
3. At least one complete owner-authorized app integration, with actual externally verified final action/effect and no unapproved side effects.
4. At least one restart/recovery and encrypted backup restore of real local state.
5. Multi-provider boundaries verified against supported authentic accounts; fallback remains non-destructive and cost-bounded.
6. End-to-end orchestration, review, data retention, negative-path security tests, packaged UX and independent operator review passed.

These gates must be satisfied by evidence, not by passing synthetic tests alone. The project **is not yet complete** as of this record. Continue each subsequent stage by choosing the highest-impact gap, creating an exact GitHub PR, passing required CI, merging, and appending verified SHA/results to `docs/PROGRESS.md`.

**Next development direction absent actual owner-PC connection:** P1's genuinely useful approved-connector task with existing deterministic workflow and verifiable receipts. Avoid gratuitous stages devoted only to more mock tests.
