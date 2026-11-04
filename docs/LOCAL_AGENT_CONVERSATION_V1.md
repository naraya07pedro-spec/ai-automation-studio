# Slice 49 · Opt-in bounded local conversation

AI Automation Studio is an **open-source, local-first agent**, not a SaaS. Slice 49 adds **optional persistent conversation context**, separate from workflow state and not equivalent to a general-purpose long-term memory system.

## How to enable

Enable Ollama inference and local conversation storage explicitly:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-history
```

The owner must sign in on `http://127.0.0.1:8765` and check **Save this conversation on my PC** on each run to save and reuse prior turns. The checkbox is off by default. Without the server history flag, the checkbox is disabled. Regular stateless runs do not read history and do not save the prompt or answer in the new conversation database.

The database lives in `~/.ai-automation-studio/agent-conversation.sqlite3` and contains a maximum of six successful user/assistant exchanges. An older pair is discarded when the capacity is exceeded. GET `/api/local-agent-history` requires login and returns saved turns; authenticated, CSRF-protected POST `/api/local-agent-clear` with `{}` deletes saved turns. Change of model with prior saved turns fails with 409: clear first to prevent incompatible history from silently influencing a new model.

## Boundaries and warnings

- **Storage is plaintext** local SQLite (and may have WAL files). It is not encrypted, not an identity vault, not approved for secrets, sensitive personal data or actual customers. Store only synthetic or nonsensitive prompts. Deleting the logical records does not promise forensic secure erasure, including backups, OS snapshots or SQLite freed pages.
- Conversation is **single-owner and single logical thread**. Do not assume independent users or tabs are isolated from each other's stored context. The existing server-side agent semaphore serializes saved runs and clears within one process.
- The system prompt and allowlisted tools remain hardcoded; the replayed prior answers are untrusted context, never tool authority. History entries have exact roles, bounded lengths, a pinned model name and capacity. Only successful validated generated answers up to 3,000 characters can be saved.
- A model request can succeed while the subsequent SQLite write fails. The endpoint fails instead of claiming successful persistence; read-only tools still have no business side effects. Recovery and forensic deletion remain separate work.
- Running without `--enable-agent-history` does not open the history database. This feature is not remotely accessible and does not create cloud accounts, sync or autonomous/background agent execution.
- Browser shows the recent saved context as text rather than HTML and hides it at logout; that does not erase saved SQLite unless the operator explicitly deletes it.
- CI tests use deterministic model/HTTP fixtures and do **not** prove an Ollama model is installed on a user machine.

Next relevant milestones: explicit independent conversation IDs, efficient context summaries with provenance and deletion controls, bounded job state, and eventually permissioned external connectors after actual model E2E verification.
