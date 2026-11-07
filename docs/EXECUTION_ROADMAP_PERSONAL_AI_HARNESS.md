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
| **#115** | 2–8 approved-plan DAG queue with dependencies, 0–3 priorities/fairness, durable one-step claims/results, pause/resume/cancel, crash quarantine, proven-step-only owner reconciliation, authenticated `/jobs` UI | **[PR #115](https://github.com/naraya07pedro-spec/ai-automation-studio/pull/115) squash-merged `f2f4c312ed212c11a59ae95bb5349be4d277d238`; exact tested head `2b4b5a9edd8b3a318b39b36795ef036e968be0a4`; 18/18 workflows; 1,250 suite tests per Python version (83 optional skips); Chromium 58/58** | Real owner-PC queue, off-device recovery, independent downstream effects and 24/7 execution unverified |
| **#116** | Owner-imported domain-separated HMAC n8n final-step assertion bound to the Stage #114 manual handoff and existing sender ledger, strict replay/conflict/tamper checks, immutable two-status evidence history and private import UI; **zero retry or new sends** | **[PR #116](https://github.com/naraya07pedro-spec/ai-automation-studio/pull/116) merged `f9dd0620355c75da49c39ece4513fa1b502e470e`; exact CI-tested head `94350624ab40e28e5e61fb4cd3cd3c76f4ab5a59`; 19/19 workflows; 1,264 full-suite tests per Python runtime (97 optional skips); 14 dedicated tests per runtime, Chromium 58/58** | HMAC is shared-secret owner-imported attestation, not independent n8n/CRM/business success; real owner-PC/n8n/24/7 unverified |
| **#117** | Owner-only read of official n8n execution API, signed ID + pinned workflow + exact inbound delivery correlation; DNS-pinned TLS GET, durable one-shot evidence, host provenance, rate caps, low-memory startup and private browser UI | **[PR #117](https://github.com/naraya07pedro-spec/ai-automation-studio/pull/117) merged `64b29d6893b920eb74d40f3e51c98c3b56262d75`; exact tested head `458a8a1f80931f11142df1ae071208ff7c8888c8`; 20/20 workflows; focused 16/16 per Python 3.11/12/13; full 1,280 tests per Python version (113 optional skips each), Chromium 58/58** | Official n8n provider was NOT actually queried in CI; live execution, Windows recovery and business-effect evidence unverified |
| **#118** | Offline read-only six-ledger plan→workflow→sender→signed result→cached n8n execution integrity audit; harden private SQLite creation; fix Stage #111 WAL backup snapshot and prevent read-only sidecars; encrypted restore re-audit | **[PR #118](https://github.com/naraya07pedro-spec/ai-automation-studio/pull/118) merged `ef5a41ee1e998894f34271fb0c93734734a76863`; exact tested head `30dbe28f6b031aa7b433d2a05e92cb7fd1cb7a60`; 11/11 CI; dedicated 15/15 tests per Python 3.11/12/13; full 1,295 tests per runtime (128 optional skips), Chromium 58/58** | Auditor proves local chain integrity only. Actual owner Windows, live provider data, off-device restore and business-effect verification remain unverified |

The #113 improvement is **a complete engineering slice from a fake provider result to real local tools**, not live proof of the paid provider and not a general multi-tool autonomous agent. It does not obtain permission from model-supplied text, does not send email or mutate live CRM, and does not invoke tools without owner approval.

### Stage #114 engineering advance (merged, CI verified)

An owner-approved **plan-provenance → deterministic-workflow receipt → encrypted managed connector** handoff has been implemented and merged to `main`. It adds a durable local evidence binding, independently password-approved HTTPS one-shot send, no-retry uncertainty state, private UI, and new test coverage. It **does not** translate agent results into a business event or independently prove downstream effects. CI passed on the exact PR head, but real owner-PC/provider and downstream acceptance are still pending. Full scope: `docs/AGENT_CONNECTOR_HANDOFF_V1.md`.

### Stage #115 implementation (merged; CI verified)

The owner-activated **Durable Multi-Plan DAG Queue** at `/jobs` adds 2–8 existing approved plan nodes per job, explicit per-node dependencies, bounded priority with fairness between equal-priority jobs, SQLite step claims and receipt digests, owner-authenticated one-step execution, pause/resume/cancel, and crash quarantine with proven-step-only owner reconciliation. **No automatic background dispatch, no model-granted write permission, no real provider/network success claim.** See `docs/DURABLE_PLAN_QUEUE_V1.md`. PR #115 merged after 18/18 successful workflows on the exact tested head; provider-live and owner-PC acceptance remain unverified.

### Stage #116 · Signed manual handoff outcome receipts (merged; CI verified)

Connect the manual owner-approved Stage #114 handoff to a **domain-separated, HMAC-valid n8n final-step claim**, matching the existing immutable per-connector delivery ledger and protecting against swapped delivery IDs, tampered payload/destination, forged or conflicting claims, replay and blind retries. Import and list via the existing private `/handoff` UI and owner-authenticated API. This remains an **owner-imported shared-secret claim**, **not independent provider API attestation** or business success. See `docs/SIGNED_MANUAL_HANDOFF_OUTCOME_V1.md`; PR #116 merged after 19/19 GitHub Actions checks; real provider and owner-PC acceptance remain unverified.

### Stage #117 · Owner-scoped independent n8n execution lookup (merged and CI verified)

Implemented a separately opted-in official n8n GET execution verifier that requires an exact Stage #116 signed receipt and matches the provider execution ID, an owner-pinned n8n workflow ID, and exact original delivery/workflow/event IDs within one pinned incoming Webhook node. It stores metadata digests only, has fail-closed SSRF/privacy constraints, 8/day + 100 lifetime caps, one owner-password-confirmed request per signed plan and no model/other connector side effects. It can run **without Ollama or OpenAI** using the existing local plan store. This verifies the **n8n execution/input association**, not independent downstream business completion. Full runbook and limits: docs/N8N_READONLY_EXECUTION_VERIFY_V1.md. PR #117 merged after 20/20 workflows succeeded on the exact tested head. Real owner-PC and live n8n API acceptance remain outstanding.

### Stage #118 · Owner-PC acceptance and offline six-ledger provenance gate (merged, CI verified)

Added a **real executable offline owner acceptance auditor** for one already-completed Stage #114→#117 handoff, not another claim of autonomous agent capability. After clean shutdown it recomputes plan/workflow/payload digests, matches the actual connector send ledger, Stage #116 signed claim, and saved Stage #117 provider record, with explicit owner-pinned n8n host/workflow/node checks. No keys, prompts or provider response data are exported; no network access, automatic retry or SQLite mutation. Dedicated tests include synthetic encrypted managed connector, signed completion, simulated n8n GET, adversarial evidence edits, and real encrypted backup plus disposable restore then re-audit. The correct conclusion is **locally consistent stored evidence only**, not fresh live n8n or independently proven business success. Runbook: `docs/OWNER_LIVE_ACCEPTANCE_GATE_V1.md`. PR #118 merged with 11/11 workflows successful on the exact tested head. Actual owner-PC/n8n and independent business-effect acceptance remain unverified.

### Stage #119 · One-owner-command live n8n acceptance gate (PR CI pending)

Add a **Windows-usable owner CLI** for a real, separately approved n8n read-only API acceptance sequence. Two explicit subcommands: `inspect` validates the local HMAC signed receipt, numeric execution ID, exact n8n host/workflow/input node pins, and no prior provider lookup via the existing authenticated localhost API without making an n8n GET; `verify` requires a hidden owner password and typed consent for exactly one Stage #117 provider GET. No generic URLs, proxy, redirects, write endpoints, repeated retries, new webhook deliveries, key export or fabricated business-success labels. The CLI treats a confirmed provider error as a **failed** job for shell exit status. No model/VPS needed. Actual Windows owner and real provider API execution remain unverified until the owner runs it. Guide `docs/OWNER_LIVE_N8N_ACCEPTANCE_V1.md`. Exact PR merge and test evidence pending.

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
