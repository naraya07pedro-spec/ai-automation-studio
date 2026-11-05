# Slice 70 · Reviewed comparison of 2–3 selected local documents

This adds a practical multi-source read-only task to AI Automation Studio's durable, owner-reviewed plan workflow. Previously the app could analyze 2–3 selected text/Markdown files in a **transient one-shot Q&A**, and separately could store **one selected text file** with a reviewed plan. This release joins the multi-document analysis engine with the existing approved-plan ledger.

## Start with separate explicit opt-ins

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-multi-documents
```

Log into the existing localhost owner UI. Under **Plan a small local task**, select **either** one text/Markdown file in the existing single-document picker **or** 2–3 distinct files in the new comparison picker. Do **not** select both. The comparison set allows at most 3 files, up to **2,500 UTF-8 bytes each** and **6,000 bytes total**. Files must have safe `.txt`/`.md` basenames, no duplicate filenames (case-insensitive), empty files, invalid control characters, invalid UTF-8, or nested paths.

Explicitly check **I authorize storing all selected comparison files**. Only selected file contents are read by the browser and sent to the authenticated loopback API. The browser checks payload size against the existing 8,192-byte HTTP body limit. The backend independently validates every source, and **no planner inference occurs if selection is invalid**.

> **Storage trade-off:** All 2–3 source snapshots are persisted **in plaintext** in the owner-local SQLite task plan. This differs from temporary one-shot comparison. Do not use credentials, confidential customer information, sensitive personal records, or any source you would not want stored on this PC. Deleting a plan cannot guarantee secure erasure of SQLite pages, WAL history, backups or process memory.

## Proposed plan and execution

The local planner is given the **goal plus safe source metadata only**: filename, UTF-8 byte count and exact SHA-256 digest for every file. It is **not** given source bodies during plan proposal. A fixed additional allowed step is:

```json
{"tool":"local_multi_document_question","args":{"question":"What time is the meeting in the selected project documents?"}}
```

The model cannot choose file paths, fetch arbitrary URLs or provide additional tool arguments. It may include calculator, UTC clock or separately operator-granted public GitHub issue steps in a **maximum three-step** plan. Existing note-memory references remain optional and separately owner-selected. The comparison set must actually be used by the plan; otherwise the request is rejected rather than silently saving unrelated documents. Source metadata and exact planned question appear in the owner review screen. No source body appears in initial plan GET/list responses.

The owner must click **Approve reviewed plan**, then manually **Execute next step** for the comparison. Only at that moment is the stored snapshot passed to the existing deterministic lexical retrieval and local Ollama citation-checking engine. This engine picks up to five bounded, keyword-matched overlapping passages, requires source-specific, exact substring quotes (with file/character provenance), and rejects unsupported literal numeric claims. If no lexical words match, it returns an explicit insufficient-evidence result without calling Ollama, **not** a claim that facts are absent from the files.

The verified-shape result is saved in the plan ledger and displayed in the plan detail. **Source-exact quote matching does not prove the interpretation is factually correct**. The stored result may expose excerpts from the operator's files in its citations and retrieval evidence; the raw snapshots are never separately returned by the plan listing API. Once steps are completed, the existing **Task Deliverable** can produce a bounded report with evidence quote membership and explicitly **unverified** interpretations, with copy and owner-triggered Markdown download.

Restart recovery retains snapshots and completed results. An interrupted operation never auto-retries; it requires explicit owner resume. If the app is restarted without `--enable-multi-documents`, the multi-source step fails closed rather than silently granting access.

## API

```http
POST /api/local-agent-plan-create
{
  "model": "YOUR_INSTALLED_MODEL",
  "goal": "Compare the two selected notes for meeting time",
  "documents": [
    {"filename":"briefing.txt","content":"The meeting starts at 18:30 WIB."},
    {"filename":"runbook.md","content":"Check the briefing before scheduling."}
  ]
}
```

The optional `documents` key can coexist with existing explicit `memory_ids` but **cannot** coexist with the existing single-file `document` key. It is rejected (422) when both are provided, malformed, duplicate, invalid or oversized. Without the multi-document opt-in it is rejected (403). Source contents are revalidated inside SQLite storage, and per-file SHA-256 digests are verified on each plan retrieval/execution. Existing plans migrate by adding an empty `document_set_json` column and retain all prior data and read permissions.

Authenticated plan responses include `document_set_sources` metadata (name, byte count, digest, plaintext flag) or an empty array. There is no additional unauthenticated file reading, filesystem scan, PDF extraction, vector index, cloud model, shell execution, GitHub write, automatic email, autonomous loop or network permission. This is a bounded read-only comparison tool, not unrestricted agent access.

## Verification boundaries

Ordinary CI verifies deterministic validation, owner/CSRF authorization, SQLite migration/restart and corruption checks, synthetic model payload boundaries, and Chromium browser interactions against **explicitly synthetic fixture responses**. These do **not** establish real Ollama compatibility, device latency, reliability or quality on the owner's Windows computer. The owner's **build-first decision** continues: real Windows/Ollama acceptance stays deferred on [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).

See [single-file task-plan sources](SELECTED_DOCUMENT_PLAN_WORKFLOW_V1.md), [transient multi-file retrieval](LOCAL_MULTI_DOCUMENT_RETRIEVAL_V1.md), and [evidence-backed Task Deliverables](COMPLETED_TASK_DELIVERABLES_V1.md).
