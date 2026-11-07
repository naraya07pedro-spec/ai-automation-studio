# Stage #114 · Completed agent plan → owner-approved encrypted connector handoff

Date: 2026-11-07 WIB. Stage scope: **no Tencent VPS, no Ollama requirement, no real OpenAI charges and no live n8n traffic during CI.**

## Delivered path

1. In Mission Control section 14, optionally propose a **bounded** cloud AI plan, separately approve it in section 02, and execute until its local state says `COMPLETED`. Existing local plans are also eligible. This new stage does not call a provider.
2. In section 04, create/activate a deterministic rule workflow and run one event. It produces a separately persisted local workflow receipt. **The agent did not generate or authorize this business event; the owner explicitly associates two preexisting records.**
3. Enable the existing AES-256-GCM managed webhook vault with a separately preserved master key. Register an allowlisted HTTPS destination via the existing owner-only UI. The legacy **plaintext** connector mode is deliberately ineligible for agent handoff.
4. Open `http://127.0.0.1:8765/handoff` from Mission Control. Choose one completed plan, one encrypted managed connector, an existing workflow, and an already-recorded event ID. The server checks the plan's persisted status and exact results, the workflow receipt, then creates a **PREVIEW_ONLY** connector token with a five-minute expiry. The new SQLite handoff ledger records plan SHA-256, workflow SHA-256, payload SHA-256, and the hashed preview token, but never the token itself or plan contents.
5. Review the HTTPS destination hostname, actual JSON payload and both evidence digests. A separate owner-session + CSRF + **fresh owner password** + explicit browser confirmation is required for exactly one commit. The model has no permission to call this endpoint and its read-only steps cannot dispatch a connector.
6. Before dispatch, the handoff ledger irreversibly claims `IN_FLIGHT`. The existing managed connector then atomically claims its own immutable delivery ledger. On transport success the status can reach `REMOTE_ACCEPTED`. Timeouts/unknown results remain `OUTCOME_UNCERTAIN` or `REVIEW_REQUIRED`. A restart or repeat click never retries the same handoff.
7. Review `GET /api/agent-connector-handoffs` and `GET /api/managed-connector-history?connector_id=...`. These are authenticated local reads with **zero external requests**. Reconcile disputed business outcomes independently in the downstream system.

## Explicit payload and evidence limits

- The sender uses the existing `aas.local_workflow_receipt.v1` schema: workflow ID, pseudonymous event ID, immutable workflow version, deterministic decision/reason, timestamp and delivery ID. **No plan goal, steps, results, API key, connector secret or model text is sent.**
- The plan SHA-256 is a local provenance linkage. It does **not** establish that the plan generated, validated or acted upon the business event. The owner chooses the association; the step result is **not** automatically transformed into a workflow input.
- A webhook HTTP 2xx means only that the endpoint accepted the request; it does not prove n8n completed, CRM updated, an email delivered or a customer replied.
- This stage does not implement arbitrary model tools, dynamic MCP/plugin writes, automatic submission of job applications, Gmail sending, unbounded agent delegation, strict provider USD billing caps, multi-provider failover or actual 24/7 runtime.
- A crash between two SQLite files' commits can create an `IN_FLIGHT` or `REVIEW_REQUIRED` state even without a network send. **Do not automatically replay these.** Human reconciliation or a separately designed recovery proof is required.
- The handoff ledger and underlying plan/workflow SQLite state are local plaintext, protected by owner-device permissions. The connector secrets live separately in the AES-256-GCM registry with a private external master key. Existing offline backup/recovery limitations still apply.
- The bounded handoff is enabled only when the running server has both a durable plan store and the **encrypted** managed connector manager. For the cloud-only profile, the plan store comes from the existing `--mode cloud-only --enable-scheduled-openai` setup; the optional encrypted webhook feature requires its own launch setting/key. No startup action automatically sends a webhook.

## Concrete acceptance

`tests/test_agent_connector_handoff.py` tests **real temporary SQLite**, a real encrypted connector registry, model-independent completed deterministic tools, and an actual authenticated HTTP listener. It injects a fake HTTPS sender and uses no real external URLs. Cases include one-shot send, never exposing plan contents, incomplete plan denial, source changes, old token invalidation, same event bound to second plan denial, restart replay protection, network timeout uncertainty, encrypted-only enforcement, and CSRF/current-owner-password gates.

Run locally on a cloned checkout with compatible Python and pinned optional encryption dependency:

```bash
python -m pip install -r requirements-vault.txt
python -m unittest discover -s tests -p 'test_agent_connector_handoff.py' -v
```

CI: `.github/workflows/agent-connector-handoff.yml` executes tests and relevant regressions on Python 3.11, 3.12 and 3.13, plus Python and browser JavaScript syntax checks. Full repository regressions and browser smoke checks should also pass on the exact PR head before merging.

## Real-world activation gate

`LIVE_OWNER_PC_HANDOFF_VERIFIED=FALSE` until the owner runs a completed plan and actual saved workflow on their Windows PC, sees a valid preview, authorizes one harmless HTTPS/n8n test endpoint, checks its independent execution outcome and matches both local ledgers. `LIVE_OPENAI_MODEL_VERIFIED=FALSE` and `ONLINE_24_7_VERIFIED=FALSE` remain unchanged.

## Next stage

After this concrete bridge, prioritize dependency-aware durable job orchestration and explicit, independently verified connector outcomes. Do **not** mistake adding more model tools for closing the gap between HTTP acceptance and business success.
