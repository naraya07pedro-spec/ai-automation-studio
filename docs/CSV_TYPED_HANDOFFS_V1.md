# Slice 76 · Typed numeric CSV handoffs in reviewed local tasks

## Product capability

A single, explicitly consented CSV (Slice 75) can now feed an **exact numeric result** into the calculator of a **later step in the same previously reviewed plan**. This removes the need for the model to manually transcribe values from a prior tool response.

An example fixed two-step plan (the local model *proposes*, and the human reviews and approves):

```json
{"steps":[
  {"tool":"local_csv_profile","args":{"columns":["revenue","stage"]}},
  {"tool":"calculate","args":{
    "a":{"from_step":1,"field":"column_sum","column":"revenue"},
    "b":"2","operation":"multiply"}}
]}
```

If the owner-selected `revenue` CSV column contains 10, 20 and one blank, the first step produces a deterministic `column_sum` of 30, and the second produces `60`. Both steps must be approved via the original reviewed plan and individually clicked, or separately authorized as one bounded server-owned batch **while the local server is alive**.

## Strict reference language

The only new valid reference shapes are:

- `{"from_step":1,"field":"row_count"}`, returning all CSV data rows including incomplete records.
- `{"from_step":1,"field":"column_nonempty_count","column":"revenue"}`, returning the number of nonempty entries.
- `{"from_step":1,"field":"column_missing_count","column":"revenue"}`, returning the number of empty entries.
- `{"from_step":1,"field":"column_distinct_count","column":"revenue"}`, returning the number of distinct nonempty entries.
- `{"from_step":1,"field":"column_sum","column":"revenue"}`, returning the exact Decimal sum **only** when the entire selected column is numeric under the existing Slice 75 bounded numeric grammar.

`from_step` is ONE-BASED and **strictly earlier** than the calculate step. `column` must be one of the **exact columns selected in that prior CSV profiling step**, not merely a column present elsewhere in the file. Row count must not include a column key. No formula strings, arbitrary JSON paths, field chaining, private record values, text extraction, percentages, mean, model-invented metrics or unsafe dynamic expressions are accepted. Existing previously supported calculator and public-issue-triage handoff formats remain unchanged.

## Fail-closed verification and recorded evidence

1. Plan proposal sees only CSV metadata (filename, row count, header names, byte size and SHA-256); **CSV rows are never sent in the model planning request**. `validate_steps` checks exact typed reference schema before any plan storage or execution.
2. The stored original CSV snapshot is revalidated against its SHA-256 when the plan is loaded. Every later calculator that references CSV checks the feature's `csv_profiler` capability remains enabled. No access without the original selected CSV and reviewed earlier profiling step.
3. Before dispatching the calculator or marking it `IN_FLIGHT`, the runtime recomputes the entire deterministic output from the frozen CSV and its originally approved selected columns. It compares the **complete saved JSON profile** with the recomputed result, not just the referenced metric. Tampered result JSON, wrong source digest, changed snapshot, nonnumeric sum, zero divisor, missing or reordered source, wrong column or numeric overflow is blocked **without** running the next tool, creating a new result, or incrementing the durable plan index.
4. The saved lineage record includes exact resolved calculator arguments, one-based source index, source tool/field, selected column (when applicable), decimal value, SHA-256 of saved tool output, and original CSV snapshot SHA-256. On plan reload or restart, the ledger recomputes and validates the full lineage and detects audit mutations.
5. Result rows and raw cell values never appear in the plan public JSON response; only aggregates and metadata. The full original CSV **does exist in local plaintext SQLite**. Do not attach secrets, credentials, personal or confidential customer records.

## Limitations

This is a **read-only numeric dependency between at most three reviewed steps**, not an autonomous planning engine, general workflow language, SQL/dataframe engine, multi-file join, spreadsheet editor, arbitrary file write, or route to a remote service. The existing 6 KB / 100-row / 8-column CSV limits remain. A saved aggregate is only as meaningful as the original data and the defined missing-value semantics. Numeric lineage protects calculation integrity, not source-data truth.

Use the existing `--enable-local-agent --enable-agent-plans --enable-plan-csv` opt-in. Synthetic Python, Windows and Chromium tests verify typed references, normal/invalid sums, overflow, tampering, feature revocation, audit persistence and reviewed browser execution. Owner-device Windows/Ollama acceptance remains deferred in open Issue #68; do not claim it passed.
