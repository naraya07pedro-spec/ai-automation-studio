# Slice 66 · Owner-curated local memory workspace (build-first)

## Delivered capability

AI Automation Studio has short opt-in conversation history, but no reusable reference knowledge across conversation threads. This feature adds **owner-curated local memory cards**, managed through the existing authenticated browser. It is intentionally **not** automatic AI memory extraction, semantic search, or unrestricted access to the PC.

Start the workbench only if desired:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-memory
```

The new flag requires `--enable-local-agent`. It is separately opt-in from `--enable-agent-history`. Neither history nor memory is enabled unless its own flag is given.

### In the browser

1. After local owner login, find **Saved memory cards** inside **Ask your local AI agent**. A card has a lower-case identifier (`1–32` ASCII letters/numbers/underscore/dash, starting with a letter or number) and human-typed note text (`1–600` characters).
2. Save a new card; it starts at revision 1. List cards to inspect them, or load an existing ID to edit. Overwrite prompts the owner for confirmation. The server requires the currently observed revision, so a stale browser cannot silently overwrite a newer card. Delete also requires an explicit owner confirmation and exact revision.
3. For the **next specific chat request only**, enter one to three comma-separated saved IDs in **Use saved note IDs**. The HTTP request passes those IDs; the server checks each ID exists and fetches a snapshot. No ID means **no memory**. Memory does not silently leak across chats or requests. The chat result reports `memory_ids_used` and `memory_source: OWNER_CURATED_UNVERIFIED`, not a model-claimed memory provenance.
4. A selected note is provided as **unverified owner-written reference data** in a separate user message, marked not to be treated as commands or authority. The user prompt and existing bounded read-only tool policy remain intact. The model's response is unverified.
5. To stop using a note, remove its ID from the chat field. Delete removes the local note from the memory store. It **does not retroactively erase** note content from a response, previously saved chat history, OS backups, Ollama processes, or logs outside this feature. Existing conversation history does not automatically save the memory body again.

### Storage and limits

- Local plaintext SQLite: `~/.ai-automation-studio/agent-memory-cards.sqlite3` with fixed columns `card_id`, `body`, `revision`, no model metadata or automatic AI mutations. Up to **24 cards**; selected context is capped at **3 cards × 600 characters** per chat request. Memory listing has bounded output.
- SQL transactions use synchronous FULL, journal_mode DELETE, thread locks, parameterized statements, a bounded busy timeout. POSIX file/parent permissions are restricted where available; Windows access continues to rely on OS account/ACL controls.
- Only an authenticated session may list memory; modifications require existing owner + CSRF + loopback/same-origin checks. Invalid body fields/IDs or revisions are rejected. `memory_revision_conflict` prevents stale replacements and deletes. Malformed/corrupted storage fails rather than overwriting old content.
- Memory CRUD never dispatches a model or writes outside the local SQLite store. This feature adds no cloud service, browser scraping, email sending, external write, arbitrary filesystem crawler, or autonomous tool capability.

### API (local owner session)

```http
GET /api/local-agent-memory
POST /api/local-agent-memory-save
{"id":"project_goal","text":"Finish the core agent first","expected_revision":0}
POST /api/local-agent-memory-delete
{"id":"project_goal","expected_revision":1}
POST /api/local-agent-run
{"model":"YOUR_INSTALLED_MODEL","prompt":"Recall my selected project goal","memory_ids":["project_goal"]}
```

`expected_revision=0` creates a new note; revision 1+ replaces the exact current version. Missing selected IDs stop chat **before inference**. The last POST is only an example of a user-selected inference request, **not** a test executed during this slice.

### Important trust and acceptance limits

Plaintext memory is unsuitable for credentials, confidential customer records, legal/medical secrets or any information the owner would not want a local process with OS-user permissions to read. A saved note is a **user assertion**, not a verified fact; a malicious note can still influence model output despite instructions to treat it as data. No cryptographic attestation, semantic truth validation, vector index, automatic relevance routing, cross-device sync, per-card encryption or system-wide content deletion is provided.

**Build-first decision:** the owner explicitly deferred actual Windows/Ollama inference testing until the core product is built. CI may exercise store behavior, HTTP authorization and the real Chromium UI with **injected fixture model responses**. These tests are NOT proof that a real Ollama model runs on the owner's Windows hardware. The deferred acceptance remains tracked in [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68), while [Issue #69](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/69) prioritizes core product completion.
