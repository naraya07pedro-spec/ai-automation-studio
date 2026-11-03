#!/usr/bin/env python3
"""Validate synthetic AC01–AC04 fixture invariants, NOT runtime behavior.

This standard-library-only checker is a documentation test utility, not
a decision on the eventual application language or architecture.
"""
import json
import sys
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "lead_inquiry_ac01_ac04.json"


def validate(data):
    errors = []
    cases = data.get("cases")
    if data.get("schema_version") != "fixture-contract-v1" or not isinstance(cases, list):
        return ["invalid fixture schema"]
    if [case.get("id") for case in cases] != ["AC01", "AC02", "AC03", "AC04"]:
        return ["case IDs must be exactly AC01-AC04 in order"]
    a, b, c, d = cases
    for case in cases:
        if case.get("input", {}).get("mode") != "mock":
            errors.append(f"{case['id']}: expected mock input")
        if case.get("expected", {}).get("external_dispatch_count") != 0:
            errors.append(f"{case['id']}: external dispatch must be zero")
    if a["expected"].get("activation_status") != "DRAFT_VALID" or a["expected"].get("validation_errors") != []:
        errors.append("AC01: expected valid configuration")
    if not a["input"]["configuration"].get("mapping"):
        errors.append("AC01: missing required mapping")
    if b["input"]["configuration"].get("mapping", "NOT_NULL") is not None or b["expected"].get("activation_status") != "BLOCKED" or "mapping_missing" not in b["expected"].get("validation_errors", []):
        errors.append("AC02: missing mapping must block activation")
    if b["expected"].get("secrets_in_output") is not False:
        errors.append("AC02: secret-redaction expectation missing")
    events = c["input"].get("events", [])
    event_ids = [(c["input"].get("source"), event.get("source_event_id")) for event in events]
    if not event_ids or any(not pair[1] for pair in event_ids):
        errors.append("AC03: event IDs missing")
    elif len(set(event_ids)) != c["expected"].get("distinct_logical_events") or len(event_ids)-len(set(event_ids)) != c["expected"].get("duplicate_event_count"):
        errors.append("AC03: inconsistent dedup count")
    if c["expected"].get("maximum_authorized_dispatches_per_logical_event") != 1:
        errors.append("AC03: dispatch ceiling must be one")
    inquiry = d["input"]["inquiry"]
    for field in d["input"]["required_qualification_fields"]:
        status = "UNKNOWN" if inquiry.get(field) is None else "VERIFIED_FROM_INPUT"
        if d["expected"]["field_assessments"].get(field) != status:
            errors.append(f"AC04: inconsistent assessment for {field}")
    if d["expected"].get("qualification_status") != "NEEDS_REVIEW" or d["expected"].get("fabricated_verified_fields") != []:
        errors.append("AC04: must require review without fabrication")
    return errors


if __name__ == "__main__":
    try:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("FAIL:", exc, file=sys.stderr)
        raise SystemExit(1)
    problems = validate(fixture)
    for problem in problems:
        print("FAIL:", problem, file=sys.stderr)
    if problems:
        raise SystemExit(1)
    print("PASS: synthetic fixture invariants AC01-AC04 (no application execution)")
