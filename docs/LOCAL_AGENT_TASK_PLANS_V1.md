# Slice 52 · Durable, operator-approved read-only AI task plans

AI Automation Studio is an **open-source, local-first agent workbench**, not SaaS. This release introduces a real bounded agent workflow: a locally installed Ollama model proposes a task plan, the operator inspects the exact permitted actions, approves it, and triggers **one** step at a time. The state survives process restarts. No model-generated instruction is permission to execute arbitrary software.

## Enable and use

First install and start Ollama separately, with a model that supports JSON-format chat responses. Provision the local owner before running the server.

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans
```

You can additionally use `--enable-agent-history` for the unrelated opt-in conversation feature, but it is **not required** for task plans.

At `http://127.0.0.1:8765`, sign in, enter the name of your installed model in the **Ask your local AI agent** model field, and find **Plan a small local task**.

1. Enter a goal such as "Multiply 75 by 28, then read the current UTC time."
2. Select **Propose a plan (no execution)**. The model creates JSON, but the server rejects more than 3 steps, malformed arguments, unrecognized tools, and arithmetic beyond the calculator's bounds.
3. Review the **full plan, tool names and exact arguments**. Press **Approve reviewed plan**. Approval changes the state but **executes zero steps**.
4. Press **Execute next step** once per step. Review each result before continuing. There is **no automatic execution** after approval and no background agent worker.
5. Use **List saved plans** and **Load plan** to resume reviewing after a restart. **Cancel plan** blocks future steps. Completed, cancelled or blocked plans can be deleted by the owner to free storage capacity.

## Exact limits and behavior

- The supported tools are the existing **bounded Decimal calculator** and **read-only UTC clock**. No arbitrary file reads/writes, shell, browser automation, third-party apps, email, data deletion, API mutations or MCP plugin dispatch.
- The model uses the existing fixed-loopback `/api/chat` transport with `format: json`, no tool permissions during the planning request. A proposed plan of **1–3 validated steps** is saved as `PREPARED`, never executed directly.
- Status progression: `PREPARED → APPROVED → NEXT_READY → COMPLETED`; every transition into a step requires one new authenticated, CSRF-protected human request. `IN_FLIGHT` is written before each tool call. A crash leaves that step `INTERRUPTED` after startup and **requires a separate explicit resume and a separate next-step action**. Because these two tools are read-only, an interrupted step can be repeated without external business writes; this policy must be redesigned before write-capable actions are allowed.
- The system keeps at most **12 plans**, each goal at most **1,200 characters**, at most three steps and at most one executing model/plan operation at a time within the current server process. Full storage rejects creation instead of silently removing another task. Delete a terminal plan to release capacity.
- The separate plaintext SQLite file is `~/.ai-automation-studio/agent-task-plans.sqlite3`. It contains the user's goal, model, complete proposed arguments, results, timestamps and status. It is not encrypted or suitable for secrets/customer PII. Logical deletion does not guarantee forensic secure erase of SQLite freed pages, WAL files or backups.
- The server must be explicitly started with **both** `--enable-local-agent` and `--enable-agent-plans`. This does not enable third-party tools or create cloud accounts. It remains bound to localhost. The owner session and CSRF safeguards protect the new APIs.

## HTTP API

All endpoints require the local owner session; POST endpoints additionally require CSRF and same-origin. On a disabled server, plan operations return `agent_planning_disabled`.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/local-agent-plan-create` | `{"goal":"text","model":"installed-model"}`; real local model proposes and persists a validated **PREPARED** plan |
| `GET /api/local-agent-plans` | List up to 12 saved plans |
| `GET /api/local-agent-plan?plan_id=...` | Read an exact saved plan, its arguments, state and results |
| `POST /api/local-agent-plan-approve` | `{"plan_id":"..."}`; approve, no execution |
| `POST /api/local-agent-plan-next` | `{"plan_id":"..."}`; execute exactly one approved step |
| `POST /api/local-agent-plan-resume` | `{"plan_id":"..."}`; acknowledge an interrupted read-only step, **does not execute it** |
| `POST /api/local-agent-plan-cancel` | `{"plan_id":"..."}`; cancel a stoppable plan |
| `POST /api/local-agent-plan-delete` | `{"plan_id":"..."}`; delete an already terminal plan |

Only exact plan IDs from local storage work. The browser shows output as `textContent` rather than rendering model-generated HTML.

## Evidence and limitations

Python tests use an injected synthetic Ollama JSON response to validate the real parsing, tool allowlist and persistent state engine. HTTP tests run the actual loopback server with a disposable local owner. Chromium tests click the real browser buttons, but planning uses an **injected fake model**. These tests do not prove any model has been installed on a particular Windows PC. The real Ollama acceptance from [Slice 51](REAL_OLLAMA_READINESS_V1.md) is still required.

This is a narrowly scoped **functional local agent plan executor**, not an autonomous researcher, cross-app integration orchestrator, production security boundary or unrestricted agent runtime.

**Next meaningful milestones:** test a real installed Ollama model and monitor RAM/latency; add an explicit permission schema and a single practical read-only external connector before considering business-side effects. Do not conflate more mock tests with expanded usable capabilities.
