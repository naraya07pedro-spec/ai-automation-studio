# Slice 75 · Owner-selected CSV profiling in reviewed task plans

## Real capability

An authenticated owner can select exactly **one short local CSV**, explicitly consent to storing that file in the existing plaintext task-plan SQLite ledger, and ask a local Ollama planner to propose a **bounded, read-only `local_csv_profile` step**. The planner sees **only** filename, UTF-8 byte length, SHA-256, row count and header names. Individual CSV cell values are not provided to the planning model.

The owner reviews the exact selected columns, approves the immutable plan and separately clicks **Execute next step** or authorizes the existing bounded server-run sequence. Only then does a deterministic, no-model/no-network CSV profiler read the persisted consented snapshot and save its column statistics to the task ledger. The result is usable in the existing owner-triggered final report and optional, separately approved local Markdown export.

## Enable and use

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-plan-csv
```

Under **Plan a small local task**, select **Or choose ONE CSV for deterministic column profiling**, check the specific plaintext-storage consent, enter a goal and choose an installed Ollama model. The model may propose `{"tool":"local_csv_profile","args":{"columns":["revenue","status"]}}`; every proposed column must be present in the actual selected CSV. Nothing is executed by proposal or approval alone. The model cannot choose paths, filenames or different records, and this tool cannot send data to outside systems.

A CSV attachment is mutually exclusive with both the one-document source and 2–3-document comparison source. The same plan may still include other separately permitted read-only steps, at most 3 total.

## Strict file contract

- Exactly one UTF-8 `.csv` with a safe basename (ASCII letters/numbers followed only by allowed letters, numbers, spaces, underscore, dot, parentheses or hyphen). Max **6,000 UTF-8 bytes**.
- Comma-separated CSV; strict quoted-field parsing, a header and **1–100 data rows**, **1–8 columns**, fixed width across rows, max **120 characters per cell**. No embedded controls, newlines/tabs within cells, malformed quoting, duplicate case-insensitive column names, or unsupported header syntax.
- Header names must match `[A-Za-z][A-Za-z0-9_]{0,31}` with no duplicates. Headers are source data, not model instructions.
- Profile step args must have exact shape `{"columns":["column_name"]}`, selecting 1–8 unique existing column names. Unknown columns or source-less CSV actions are rejected before dispatch.
- Metrics per selected column: nonempty count, missing count, distinct **nonempty** value count, `numeric` vs `text_or_empty`. If all present values are bounded plain decimal strings (at most 12 integral digits and 6 fractional digits, optional minus sign), compute exact Decimal min, max, sum, and mean rounded to 6 decimal places. If values are mixed or out-of-pattern, there is **no numeric summary**; do not coerce or hallucinate.
- Raw cell values are **not echoed in authenticated plan GET/list or profile output**. Metadata contains filename, source bytes, digest, row count and headers. The full selected source **is stored in plaintext SQLite**, so do not attach credentials, confidential records, customer PII or secrets. Host filesystem cannot be scanned.
- No arbitrary SQL, Python execution, CSV modifications, formula evaluation, chart engine, file writer, external APIs, row-level retrieval, scheduled tasks, cloud model fallback or unnoticed source re-read. The selected bytes are frozen by SHA-256 before owner approval. A changed/tampered persisted CSV is rejected upon plan load.

## Boundaries and evidence

The profile is descriptive statistics **only**, not semantic or financial accuracy, evidence of causation, validity of missing-value assumptions, or independent verification of customer data. Distinct counts reveal aggregate diversity, which could still be sensitive with very small datasets. Review source privacy before consent. The final model-generated report, if requested, is independently labeled unverified even when exact output quotes match.

GitHub CI synthetic Python, HTTP and Chromium tests validate parsing, strict permissions, source privacy, SQLite persistence/reopen, data tamper refusal, deterministic arithmetic, owner consent and final report flow. These tests do **not** establish Ollama compatibility on the owner's real Windows machine. Live hardware acceptance stays deferred under [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).

**Next logical step:** Add typed numeric references from verified selected CSV counts/sums into later calculators, with strict source digest and original-plan permission rechecks. Do not add generic write-capable tools without a separate grant/review protocol.
