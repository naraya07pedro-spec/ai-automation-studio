# Slice 58 · Overlap retrieval, traceable selection, and offline comparative evaluation

AI Automation Studio is still **open-source, local-first and not a SaaS product**. This slice improves a specific observed limitation in the Slice 57 multi-document feature: fixed nonoverlapping text chunks could split a source quote across a boundary, and literal term matching missed a few simple word forms. It also makes the retrieval decision visible instead of leaving operators to guess why the model saw a particular passage.

## How retrieval now works

In the existing opt-in **Ask across 2–3 selected documents** workflow, the server keeps **450-character source windows with 96-character overlap** (stride 354). Windows retain their original characters and source offsets. The last redundant window is omitted if the prior window already covers the file end; the maximum number of passages passed to Ollama stays **five**. All previous owner login, CSRF, UTF-8 file checks, 2–3-file count, 2,500-byte per-file and 6,000-byte combined caps, no background indexing and no external writes remain unchanged.

The deterministic retrieval stage still scores candidates based on unique normalized question words shared with a source window. Additional **small, deliberately conservative normalization** handles basic English plural forms (`approvals → approval`, `policies → policy`) and the Indonesian suffix `-nya` (`dokumennya → dokumen`). Unicode NFKC and case-folding are applied to matching tokens **only**, not to stored quoted text. This is **not** a general English/Indonesian stemmer, multilingual search, semantic matching, or an embedding model; false positives and false negatives remain possible.

The model never chooses its own source permissions or arbitrary file paths. Only explicitly selected source passages are sent to fixed-loopback Ollama with **no model tools**. Exact citation validation is unchanged: the model must cite a real returned chunk and an exact substring, and the server reconstructs the original filename, file index and start/end character positions. Incorrect cross-file quotes are rejected.

## New inspectable retrieval evidence

The API now returns `retrieval: DETERMINISTIC_LEXICAL_OVERLAP_V2` and a bounded `retrieval_evidence` array alongside the generated answer. For each selected window it includes:

- source filename and chunk ID, authoritative character start/end offsets;
- integer `lexical_overlap_count` and up to 16 normalized `matched_terms`;
- a source-backed plain-text preview capped at 160 characters.

The browser displays these hits after the verified answer quotations, including the matched terms and source preview. **Overlap count is not relevance confidence, model confidence, or proof of answer accuracy.** This output contains excerpts of already selected documents and remains transient in the same authenticated response; it is not persisted to the app's SQLite database. When no words match, the response contains an empty evidence list, `model_calls: 0` and explicit warning that the answer might still exist under a different vocabulary.

## Reproducible local evaluation

Run from the repository root:

```powershell
python app/local_retrieval_eval.py
```

This command performs a **fully offline deterministic comparison** between an in-script copy of the historical Slice 57 fixed-chunk/word-matching procedure and the Slice 58 implementation, using the exact same small predeclared documents and questions. It prints JSON with `cases`, `legacy_pass`, `new_pass`, `improved_cases`, `regressed_cases`, and each case's retrieved IDs. It exits unsuccessfully if the current implementation misses any expected source span or regresses relative to the baseline.

The curated cases include exact English source selection, a quote spanning the old 450-character boundary, plural approvals/policies, Indonesian possessive `-nya`, punctuation, and a deliberately unrelated question. A *pass* means **at least one selected source window contains the exact expected quoted span in the correct source file**, not that the model correctly answers the question. The evaluation **does not invoke Ollama or collect real user documents** and cannot establish real-world retrieval precision, recall or semantic QA accuracy.

The unit regression gate covers all curated cases, source offsets, wrong-file citation rejection, trace bounds, and the no-match/no-inference path. Chromium tests use a fake model, but click the real browser and verify the trace is shown. The existing Python 3.11/3.12/3.13, Chromium, read-only GitHub and Windows ACL check gate continues to apply.

## Real hardware evidence is still separate

The real Windows Ollama acceptance from Slice 57 remains:

```powershell
python app/local_ollama_readiness.py
python app/local_multi_doc_smoke.py --model YOUR_INSTALLED_MODEL
```

Use the **actual installed model** returned by the first command. The smoke uses harmless built-in texts, verifies a citation from the correct second file, and reports wall time and *system-wide available physical RAM* before/after, **not Ollama-specific RSS or peak memory**. Neither the GitHub Actions runs nor this offline lexical evaluation establishes that an Ollama model is installed or inference works correctly on the user's Windows PC. No one should promote a fixture-model green test into a real-device success claim.

**Known limitations:** small text/Markdown inputs only, no PDF/DOCX, no vector index, no automatic reading of arbitrary files, no long-term memory or autonomous edits; a matching quote verifies a substring, not the truth of the model's answer. A human still decides how to use the reported information.
