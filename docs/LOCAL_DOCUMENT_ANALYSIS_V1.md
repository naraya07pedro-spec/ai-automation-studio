# Slice 56 · Operator-selected local text document Q&A

AI Automation Studio remains **open-source, local-first, and not SaaS**. This slice expands beyond GitHub: the signed-in operator may choose a **small local UTF-8 text or Markdown file** in the browser and ask a question. A local Ollama model proposes an answer and exact source quotes; the server verifies that each quote exists at the supplied source position before returning it.

## Enable

Prerequisites: existing local owner password, Python, Ollama started separately on fixed local port 11434, and a JSON-output-capable model installed. The app does not install or download models.

```powershell
python app/local_server.py --enable-local-agent --enable-local-documents
```

Visit `http://127.0.0.1:8765`, sign in, enter the **exact installed model name** in the existing local-agent model selector, and scroll to **Ask about a selected text document**. Choose a `.txt` or `.md` file that is genuinely UTF-8 and **at most 6,000 bytes**. Ask a question of at most 500 characters and click **Analyze selected file with local Ollama**.

The browser reads **only the file the operator explicitly chose**, via its file input. The server accepts the file's text as the JSON request body, not a filesystem path. No directory traversal, automatic scanning, shell, file overwrite, arbitrary document access, or cloud upload is implemented. The specific file contents may be sent to the user's local Ollama process via its fixed loopback HTTP endpoint for inference. This is an explicit, potentially sensitive model invocation; Ollama's own request logging, caching, weights and runtime security depend on Ollama configuration outside this repository. Do not upload credentials, private customer information or large sensitive documents.

**This feature is separately opt-in and defaults off.** `--enable-local-documents` without `--enable-local-agent` is rejected. It does not require GitHub or task-plan flags and has no access to other local files.

## Output and evidence verification

The server divides the selected document into bounded chunks of up to 650 characters (`S1`, `S2`, ...). Its local model is asked to return strict JSON with `answer`, `insufficient_evidence` and up to four `citations`. A citation is an exact `chunk_id` and a text `quote` (max 180 characters). On receipt, the server checks that the chunk exists and every quoted character occurs **verbatim within that chunk**. It reconstructs zero-based `start_char` and `end_char` offsets from the original text; it never trusts model-invented offsets.

Unrecognized fields, fabricated chunk IDs, invented quotations, malformed JSON, oversized answers, or an unsupported evidence schema are rejected. When the document does not support an answer, the model may return a fixed `insufficient_evidence: true` response with zero citations; no fake source is accepted. A source quote does **not** establish that the AI's interpretation is correct or complete. The UI marks the answer as **not fact-checked**. An injected command appearing in the document text is still untrusted data, not permission for the model to run tools.

**Retention:** The application does not create a document database, persist the document to a plan/conversation ledger, or append the answer to chat history. Output exists only in the browser response and transient server/model process memory. On logout, the browser clears the selected file, question and displayed answer. This is not a promise of secure erasure of OS swap, temporary files, Ollama internals or external backups.

## Endpoint

`POST /api/local-agent-document-analyze`, owner session + same-origin + CSRF required, `Content-Type: application/json`.

Example body:

```json
{"filename":"notes.txt","content":"Meeting starts at 09:00.","question":"When does the meeting start?","model":"installed:model"}
```

Limits: global HTTP body <= 8,192 bytes; file UTF-8 payload <= 6,000 bytes; allowed file name is a short simple `.txt` or `.md` basename; nonempty question <= 500 chars; answer <= 1,200 chars; up to four exact source citations. Requests share the existing one-inference-at-a-time semaphore, with 429 busy response. Requests are not accepted without the explicit startup flag. No external data writes, business actions or background tasks are performed.

## Verification and limitations

Python regression tests validate the Ollama JSON contract, exact citations, incorrect chunk IDs, malicious document instructions as data, size caps, invalid UTF-8 encoding, insufficient evidence, CSRF/session/default deny and concurrency. Chromium uses the actual user-facing file input with **synthetic text and an injected fake Ollama response**, and confirms the file/result are hidden on logout.

**Not yet verified on the user's Windows PC:** real model installation, model prompt adherence, inference latency/RAM consumption, accuracy on a genuine local file, and ability to answer complex questions beyond the bounded text sample. Running GitHub Actions cannot confirm these machine-specific facts.

**Next high-value step:** run a real Ollama model on Windows with a non-sensitive test file and record actual output/resource usage, then consider a separate permissioned multi-document retrieval flow. This single-document implementation is intentionally limited and should not be described as complete RAG, semantic search, PDF parsing, or unrestricted local-agent file access.
