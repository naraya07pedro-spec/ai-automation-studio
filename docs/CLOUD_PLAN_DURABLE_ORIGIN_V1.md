# Durable reviewed cloud-plan source binding

An owner-reviewed local plan proposed through the selected paid API now stores
**full 32-hex paid request ID + exact provider ID** in its existing SQLite
plan row, **atomically** with plan creation. The cloud model cannot author this
metadata. It is derived by the trusted bridge from the original validated request
and completed, provider/model/prompt-hash-bound remote receipt.

The plan GET API returns `cloud_origin` for new cloud plans, or `null` for
ordinary local plans and plans created before this feature. A recovered plan
must match the persisted provider and **entire** original request ID, even if
a different paid request has an identical first 24 hex digits (the stable plan
ID convention). A conflict returns HTTP 409; no unauthorized local plan reuse,
execution or paid retry is triggered by recovery.

Old database records are upgraded with one nullable-in-practice empty column;
existing goals, steps, approvals, results, and non-cloud plans remain unchanged.
Historical plans whose origin was not stored are returned as
`source_provenance: "LEGACY_UNBOUND"` after the existing exact receipt
binding check. The bridge must **not claim** their origin is persisted proof.
Newly bound plans return `source_provenance: "BOUND"`.

The column is local plaintext SQLite metadata, not a signature, real provider
attestation or a guarantee against malicious OS-level database edits.
The paid ledger, independent safety gate, owner password, separate approval for
each step, no remote retries after uncertainty and owner-configured attempt
caps remain in force.

Regression:
`python -m unittest discover -s tests -p test_cloud_plan_provenance.py -v`.
Includes real SQLite restart, per-step execution, no-charge receipt recovery,
adversarial request ID prefix collision, legacy source and forged data.

This does not prove real API-account availability, charges, actual Windows
hardware or Tencent VPS uptime.
