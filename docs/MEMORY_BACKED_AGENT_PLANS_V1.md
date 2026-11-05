# Slice 67 · Memory-backed, reviewed agent task plans

**Build-first feature.** This connects Slice 66's opt-in owner-curated memory cards to the existing local Ollama planner, durable review and step-by-step read-only plan execution. This is a functional integration, **not** live Windows/Ollama acceptance.

## Start and operate

Both flags must be explicit:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-memory --enable-agent-plans
```

Log into the existing loopback owner UI. Create/inspect a saved note under **Saved memory cards** (maximum 24 cards, 600 characters each). Under **Plan a small local task**, enter the locally installed model, a goal (max 1,200 characters), and **optionally** 1–3 comma-separated note IDs in **Memory IDs for THIS proposed task plan**.

The only model call at proposal time receives the chosen note snapshots as **unverified reference data** in a separate user message, not as system instructions, tool authorization or automatic recall. If the IDs field is blank, proposal behaves as before: no note content is fetched. Only the fixed allowlisted tools can appear in a validated plan (calculator and UTC clock, or separately authorized public GitHub issue readers). The actual tool execution is **not** performed when proposing or approving.

The proposed read-only steps and **exact memory IDs, revisions and SHA-256 content fingerprints** are stored in the existing bounded plaintext SQLite plan ledger. Memory note bodies are *not duplicated as separate plan metadata*, although plan steps/goal or later model-produced analysis may include information derived from those notes. This remains plaintext local data. The UI shows the selected memory references in the review pane. Do not store credentials, private records or customer PII in notes/plans.

To execute: review the proposed tool arguments and memory references; click **Approve reviewed plan**, then click **Execute next step** separately for every step. Plans remain limited to three read-only steps and never auto-resume after restart. All existing GitHub grants and versioned permission epochs still apply.

## Stale memory and recovery

A plan is valid for its **specific memory snapshot**, not any future content associated with its note IDs. The app checks each selected memory ID, revision and exact content fingerprint:

- When the model completes proposal, **before** a durable `PREPARED` plan is saved.
- Before the operator may approve `PREPARED`.
- Before each operator-triggered `NEXT_READY` or `APPROVED` step.
- Before explicitly resuming an interrupted plan.

Updating, deleting or replacing a note while keeping the same ID makes the earlier plan stale. This includes deleting a note and recreating it at revision 1 with different text: the fingerprint prevents accidental stale approval (the ABA problem). A stale plan fails **before** the next step, returns HTTP 409 `memory_context_changed`, and retains its previous status/results. The owner should cancel it, review the current notes and propose a new plan. Old approvals are never silently upgraded. Nonmemory plans (including previously stored plans) continue unchanged.

There is no global lock across concurrent SQLite databases or other Ollama clients. If a note changes **after** a step started, that in-flight read-only step is not retroactively cancelled. Future steps recheck. Content fingerprints protect accidental stale use, not deliberate tampering by someone controlling the local OS account or databases.

## API and backward compatibility

```http
POST /api/local-agent-plan-create
{"goal":"Calculate 7 multiplied by 8","model":"YOUR_INSTALLED_MODEL","memory_ids":["project_goal"]}

GET /api/local-agent-plan?plan_id=EXISTING_PLAN_ID
```

The optional `memory_ids` array is strict: 1–3 distinct, existing, lowercase note IDs. A missing memory card returns 404 **without model dispatch**; duplicate/empty/invalid IDs return 422; disabled memory returns 403. Nonmemory requests retain the two-field `goal` + `model` contract. Stored plan responses now include `memory_refs` (empty for old and nonmemory plans). Old SQLite plans migrate by adding one `memory_refs_json` column with `[]` as its default, without modifying their steps, approvals or results.

The note source remains `OWNER_CURATED_UNVERIFIED`: selecting a note does **not** establish its factual truth or its reliability against prompt injection. The model's tool proposal is still validated against strict allowlists. No arbitrary shell, filesystem scan, email, editing, new cloud provider or autonomous write is enabled.

## Verification boundary

Source-control CI validates the deterministic memory store, model-request message framing, durable plan state machine, actual HTTP authorization/CSRF and headless Chromium flows using explicitly synthetic callbacks. The user asked to **defer real Ollama/Windows testing until the core product is built**, tracked in [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68). No host model, RAM, latency or real inference result may be inferred from the CI fixtures.

See also [Owner-curated memory](OWNER_CURATED_MEMORY_V1.md) and [Durable task plans](LOCAL_AGENT_TASK_PLANS_V1.md).
