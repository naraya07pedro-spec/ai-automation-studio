# Slice 50 · Bounded isolated local-agent conversations

AI Automation Studio is an **open-source local-first agent**. This iteration isolates saved conversation context by explicit, human-selected IDs. It is **not multi-user security isolation**, a general memory system, or a hosted SaaS.

## How it works

Run the server with explicit model and history permissions:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-history
```

After owner login at `http://127.0.0.1:8765`, choose an existing ID from the conversation input's suggestions, or type a new ID such as `research` or `project-1`. Check **Save this conversation on my PC** on each turn. Only a remembered successful response contributes to that ID's stored history. Unticked prompts are stateless, do not read saved history and are not saved.

IDs are 1–32 ASCII characters, lowercase letters or digits first, followed by lowercase letters, digits, `_` or `-`. Limits: **at most 8 saved conversations**, **6 user/assistant pairs per conversation**. A ninth conversation is rejected with 409 rather than silently deleting another conversation's content. To free capacity, delete an existing conversation.

## Migration and data safety

The original Slice 49 `agent_conversation` SQLite table is retained, and a nullable-free `conversation_id TEXT NOT NULL DEFAULT 'default'` column is added **in place** if absent. Existing records become members of the `default` conversation. No original messages are intentionally dropped by migration; the pre-existing six-pair bound remains. The SQLite database is still `~/.ai-automation-studio/agent-conversation.sqlite3`. Take a backup with the local server **stopped** before upgrading a valued history store; not all power-loss and filesystem failure conditions have been exercised. Treat the database as **plaintext** and keep secrets/PII out.

## API contract

Every endpoint continues to require the existing owner session; POST calls additionally require CSRF and same-origin validation.

- `GET /api/local-agent-conversations`: list of up to 8 saved IDs, no raw messages.
- `GET /api/local-agent-history?conversation_id=project-1`: saved turns for exactly that ID. Without query, the historical `default` ID is used.
- `POST /api/local-agent-run` with `{"model":"installed-model","prompt":"text","remember":true,"conversation_id":"project-1"}` replays only that ID's model-pinned bounded context, runs the existing read-only tools, and stores the validated answer. `conversation_id` cannot be supplied when `remember` is false/omitted. Legacy stateless and remembered requests without an ID continue to work with the default.
- `POST /api/local-agent-clear` with `{"conversation_id":"project-1"}` deletes exactly that selected ID. For backward compatibility only, `{}` still clears *all* saved conversations. The browser sends the selected ID and never uses this clear-all form.

A previously saved thread cannot silently switch models: clear that thread first. All operations are bounded within one server process via the existing semaphore; readers are protected by the SQLite store lock. This is **not** cross-process locking, encrypted storage, or forensic secure erase.

## What tests prove and what they do not

SQLite migration from a legacy schema, reopening after restart, distinct histories, per-ID six-turn trimming, eight-ID capacity and deliberate deletion are covered by deterministic unit tests. HTTP regression tests check single-owner authorization/CSRF, input rejection, no inadvertent tool calls and per-thread context replay. Chromium tests exercise the actual UI with a fixture model.

**Still unverified:** an actual Ollama installation running a compatible model on a user PC, business application writes, real task planning/execution, multitenant access, cryptographic deletion, and a durable recovery queue for in-flight model calls. Those are distinct future milestones.

**Next priorities:** complete a real-model, Windows hardware E2E smoke and improve beginner setup; then durable, permissioned task plans and verifiable tool actions. Merely adding conversation threads is not evidence of an autonomous agent.
