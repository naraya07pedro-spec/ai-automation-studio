# Slice 73: server-owned reviewed read-only batch

## Product improvement
The local server can execute the remaining steps of one approved 1–3-step read-only plan. The owner must still inspect and approve every fixed tool and argument, then accept a **separate native confirmation**. The server saves a durable batch intent before launching one worker and commits each step result. Closing the browser does not stop that worker while the server stays alive.

## Actions and states
- POST `/api/local-agent-plan-batch-start` with `{"plan_id":"<24 hex chars>"}`: authenticated, same-origin, CSRF-protected, reserves the shared model gate, saves `RUNNING`, then starts exactly one worker. Repeated start while busy is denied.
- GET `/api/local-agent-plan?plan_id=...`: owner-authenticated, returns `batch_run_status` plus the existing step ledger. The browser polls when a server-run is active.
- POST `/api/local-agent-plan-batch-stop`: authenticated, CSRF-protected, durably marks `STOPPING`. The active step may finish; no subsequent step may begin. Final state `STOPPED`.
- `COMPLETED` indicates the original reviewed steps finished. `FAILED` requires inspection; the worker never retries automatically.
- On process restart, `RUNNING`/`STOPPING` becomes `INTERRUPTED`, and a plan with `IN_FLIGHT` becomes `INTERRUPTED`. The server never resumes or replays work on startup. A fresh explicit owner decision is required.

## Guardrails
The only batch tools are `calculate`, `utc_now`, `github_public_issues`, `github_issue_triage`, `github_issue_analysis`, `local_document_question`, `local_multi_document_question`. Existing source grants, memory revisions, document digests, handoff lineage, and model-safety checkpoints are enforced for each step. The single global model semaphore is held during the batch; timeout/disconnect latches the uncertain model checkpoint and stops all following steps. No writes, shell execution, arbitrary file access, unreviewed replanning, extra integrations, or automatic retry.

This is an **in-process local-server worker**, not an operating-system service, cross-process job queue, scheduled assistant, or agent that runs after the server exits. An in-flight step cannot be reliably cancelled; plaintext SQLite document snapshots remain unsuitable for secrets.

## Validation
Synthetic Python tests cover approval, completion, concurrency, stop, restart and timeout quarantine. Synthetic Chromium checks cover consent and returning to a saved plan. GitHub CI is not real Windows/Ollama acceptance; owner-device testing remains deferred in [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).
