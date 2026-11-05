# Slice 57 · Bounded multi-document retrieval and real-Ollama device acceptance

AI Automation Studio remains an **open-source, local-first, single-owner AI agent workbench**, not SaaS. Slice 57 introduces a distinct capability beyond GitHub and the Slice 56 single-file reader: the operator can explicitly select **two or three short UTF-8 text/Markdown documents**, ask one question, and get a local Ollama answer with source quotations attributed to the correct original file.

## How to use

From the latest repository, install/start Ollama separately with a JSON-output-capable model, provision the local owner if this is a first run, then:

```powershell
python app/local_server.py --enable-local-agent --enable-multi-documents
```

Open `http://127.0.0.1:8765`, log in, enter an **installed** model name into the local agent model field, then find **Ask across 2–3 selected documents**. Select exactly 2–3 **distinct** `.txt` or `.md` files, type your question and click **Analyze selected documents locally**. The original Slice 56 single-file panel has a **separate** optional flag (`--enable-local-documents`) and is unchanged.

File rules: each chosen document may contain at most **2,500 UTF-8 bytes**; total selected file content may not exceed **6,000 UTF-8 bytes**; filenames must be short plain basenames with `.txt` or `.md` extensions, with no duplicate names ignoring case. The existing global JSON request limit of **8,192 bytes** also applies. A large collection of newlines/JSON escaping may reach the HTTP body cap even if the raw file byte limit is satisfied; the browser checks the serialized request size and reports an error.

## Deterministic retrieval and grounded citations

This is not vector search or semantic RAG. The source text is divided into fixed **450-character chunks** with stable request-local identifiers such as `F1C1` or `F2C2`. The server extracts case-insensitive lexical terms from the question and each chunk, discards a small Indonesian/English stopword list, ranks chunks by the **number of overlapping distinct words**, then sends only the top **five matching chunks** to the local model. Deterministic file order and character position break score ties. There is no external search index, embedding provider, cross-session database, or folder scan. An unmatched question returns an explicit `model_calls: 0` response, **not** a claim that the answer is absent from the documents.

When chunks match, the server makes one fixed-loopback Ollama JSON inference with no model tools. The model must return `{"answer":"...","insufficient_evidence":false,"citations":[{"chunk_id":"F2C1","quote":"exact quoted text"}]}` or a strict insufficient-evidence response. The server independently validates the citation's selected chunk and the quoted text, reconstructs **original filename, file index and start/end character positions**, and rejects citations to an absent chunk, a quote taken from a different file, an invented quote or malformed fields. It accepts at most **four** source quotes of 5–180 characters and answer text of at most 1,200 characters.

**Provenance is mechanical, not semantic.** A verified exact source quote proves the quoted characters occur at the reported position in the chosen file. It does **not** establish that the AI answer is factually correct or that retrieval found every relevant passage. A word-overlap search can miss synonyms, morphology, multilingual paraphrases, tables and facts spanning chunk boundaries. The UI explicitly marks the interpretation unverified. External text is untrusted input to a model with zero tool permissions and cannot authorize file reads, writes, shell actions or external API calls.

## Data handling and security

The operator's browser reads **only the explicitly selected files** via its standard File input. It transmits file contents, question and selected model only to the authenticated `127.0.0.1` server. The server preflights exact request schema and file limits **before** entering its one-at-a-time local model gate and before invoking any analyzer implementation. The source passages and question are sent to the separately installed **local Ollama endpoint**. The feature does not scan the PC filesystem, read arbitrary server file paths, store documents/answers in the app's SQLite, or write to any external service.

`POST /api/local-agent-multi-document-analyze` requires an authorized owner session, same-origin and CSRF. The feature is disabled by default and `--enable-multi-documents` requires `--enable-local-agent`; it does not need the GitHub/task-plan flags. On logout, the browser clears selected file inputs, current question and shown answer. Changing/resetting inputs invalidates any displayed result. If a request has already reached Ollama, clearing the UI does not retroactively erase model/runtime buffers or cancel the underlying inference. Neither the OS swap nor Ollama process logging is covered by an app-level secure erasure promise. Avoid confidential materials unless your local Ollama setup and host are appropriately secured.

## Run real local inference acceptance on Windows

A new operator-initiated CLI uses **two harmless built-in text samples**, not real user files, to probe an installed model's JSON compliance, lexical retrieval and exact cross-document citation behavior:

```powershell
python app/local_ollama_readiness.py
python app/local_multi_doc_smoke.py --model YOUR_INSTALLED_MODEL
```

Use the exact installed model name returned by `local_ollama_readiness.py`. The smoke performs one real local Ollama `/api/chat` call and requires a quote that **actually contains `18:30 WIB` from `schedule.md`**, or exits with `BLOCKED`. It measures total elapsed request milliseconds and, on Windows, captures **system-wide available physical RAM before/after** via the OS. These measurements are not Ollama model RSS, peak resident memory, or latency guarantees. It does not create a persistent report, write external data, or fetch any GitHub source.

**The GitHub CI does not run installed Ollama model weights.** Synthetic test transports validate expected behavior and fail-closed outputs. A real Windows run is required to claim the user machine successfully completed this acceptance. If the model outputs malformed JSON or wrong quotes, the smoke refuses to report success, rather than faking it.

## Evidence and limitations

Unit tests verify deterministic matching/ranking, file binding/offsets, absence-of-match without model calls, fabricated-source denial, bad filename/oversized inputs, malicious in-document instructions remaining data and smoke acceptance gates. Authenticated HTTP tests verify CSRF/origin, default-disabled state, body preflight and model concurrency. Chromium acceptance selects two actual fixture files through the browser UI and checks the final answer, source file and logout cleanup. The browser and Python fixtures **use a fake model**.

Current constraints: 2–3 tiny documents, no PDF/DOCX ingestion, no folder indexing, no semantic embeddings, no automatic browsing, no background jobs and no cross-session document memory. Future expansion should prioritize validated real-device performance and practical retrieval quality before broadening source permissions.
