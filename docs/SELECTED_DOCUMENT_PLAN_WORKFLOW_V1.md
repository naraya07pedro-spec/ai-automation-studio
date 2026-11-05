# Slice 69 · Owner-selected document as a reviewed task-plan step

This connects the existing citation-checked local text Q&A capability to the durable, manually approved, read-only agent plan. It is **not** automatic PC file search or an unrestricted AI agent.

## How to enable it

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-local-documents
```

Provision and log into the existing local operator account. The document feature remains separately opted in with `--enable-local-documents`, as before. `--enable-agent-plans` without it does not permit document-backed plan steps.

Under **Plan a small local task**, enter a goal and your installed model name, and optionally choose exactly one small UTF-8 `.txt` or `.md` file. The new task-plan selection is separate from the pre-existing one-shot Q&A file picker. The maximum selected file size **for persisted plan snapshots is 3,500 UTF-8 bytes** (lower than the separate one-shot 6,000-byte Q&A limit). The app rejects nested paths, executable extensions, invalid encoding/control characters, empty files and oversized content.

**Explicitly tick the document-storage consent checkbox** before proposing. The browser reads only that chosen file and sends its text to the authenticated localhost backend. No directories are scanned, no arbitrary server path is accepted. The document content is persisted **as plaintext** with the specific task plan in `~/.ai-automation-studio/agent-task-plans.sqlite3`; the plaintext can remain until the owner deletes that plan and possibly longer in SQLite free pages or OS backups. Do not select passwords, client records, personal health data or other sensitive information.

## Controlled execution

1. The planner sees the **filename, UTF-8 byte count, SHA-256 fingerprint** and original goal, not the source document body during **proposal**. When an owner-selected source exists, a fixed new allowlisted step can be proposed: `local_document_question` with only `{"question":"..." }` (maximum 500 characters). The model cannot provide a path, filename, URL or arbitrary tool arguments as part of this step.
2. The proposal is persisted as `PREPARED`. The owner sees the **document metadata and exact proposed question** in the plan-review pane. The raw document text is **not** returned by plan GET/list APIs or included as a separate display element, though a model-authored question or later answer could paraphrase it.
3. The operator must explicitly click **Approve reviewed plan**, then **Execute next step** for that source question. The task executor reads the frozen source snapshot from its own bounded local SQLite record, not from the PC filesystem. It invokes the existing local citation-checked `analyze_local_document` function. That code validates exact quotes/character offsets and rejects unsupported literal numeric claims; interpretation remains unverified.
4. Every approved step is individually triggered, limited by the existing maximum of three steps per plan. The answer and citations become another saved plan result. After completion, the owner may explicitly generate the previously implemented **Task Deliverable**, copy it or download Markdown with source-linked findings, without automatically running additional plan steps.
5. Restarts preserve the selected document snapshot and prior plan results. Interrupted steps remain halted until an operator explicitly resumes. The document tool will refuse to proceed if restarted without the document capability enabled.

## API and safety

```http
POST /api/local-agent-plan-create
{
  "goal": "When does the service desk open?",
  "model": "YOUR_INSTALLED_MODEL",
  "document": {
    "filename": "desk-notes.txt",
    "content": "The service desk opens at 09:00 on weekdays."
  }
}
```

The `document` key is **optional**. It can coexist with the previously supported `memory_ids`; no memory or document is selected implicitly. Invalid documents fail **before planner invocation**, while a model-proposed document step without an explicitly selected source is rejected. An attached source that is not used in the proposed steps is not silently persisted. Responses provide `document_source` metadata, including filename, source byte count and SHA-256 digest, never the raw source body. A bounded human-reviewed plan may subsequently run the document Q&A step using only the attached source. This is still one plaintext per-plan snapshot, not a searchable index.

Every endpoint retains the same loopback + owner session + CSRF policy. The persistent Ollama in-flight checkpoint/quarantine still applies. The tool **cannot open arbitrary local paths**, execute shell commands, send messages, mutate GitHub issues, follow external links, change files, or grant itself new permissions. The model sees the full document only at the manually approved document-analysis step.

## Source and evidence limitations

- A verified quote proves **literal membership in the owner-supplied snapshot**, not that the text is truthful or that the generated interpretation follows logically from it.
- The plan's final Task Deliverable is a separate model synthesis of stored tool outputs; its evidence checks prove literal quote membership, not correctness.
- The owner-selected snapshot is **plaintext** in local SQLite. File deletion or modifying the original file **does not update** the frozen plan snapshot. To work from revised content, delete the old completed/cancelled/blocked plan and explicitly select the new document for a new proposal.
- Any accidental or deliberate malicious instructions in the selected file remain untrusted data. Prompt-injection risk cannot be eliminated by citation matching. Existing fixed-tool validators and human approval are still needed.
- This feature adds **one practical read-only data source** to the existing plan executor, but not general autonomous research, semantic retrieval, PDF extraction or cloud integration.
- Actual Windows/Ollama device tests remain **deferred by the owner's build-first request**, tracked in [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68). GitHub CI exercises injected synthetic local model responses and real browser/API interactions; it does not establish on-device performance.

See [existing single-document citation validation](LOCAL_DOCUMENT_ANALYSIS_V1.md), [memory-backed plans](MEMORY_BACKED_AGENT_PLANS_V1.md) and [completed deliverables](COMPLETED_TASK_DELIVERABLES_V1.md).
