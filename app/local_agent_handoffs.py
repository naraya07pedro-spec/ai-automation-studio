"""Bounded, typed cross-step numeric handoff for owner-reviewed read-only plans.

Only calculator scalars and validated deterministic public-issue triage counts
may flow into later calculator operands. No template language, dynamic paths,
arbitrary text, model-output instructions, or implicit tool execution.
"""
from hashlib import sha256
import json

from local_ollama_agent import AgentError, calculate

ALLOWED_NUMERIC_FIELDS = {
    "calculate": frozenset(("output",)),
    "github_issue_triage": frozenset((
        "returned_count", "review_flagged_count", "unassessed_count"))
}
MAX_ISSUES = 15


class HandoffError(Exception):
    pass


def _binding(value, previous_steps):
    """A typed, 1-based reference to an earlier compatible result field."""
    if (type(value) is not dict
            or set(value) != {"from_step", "field"}
            or type(value["from_step"]) is not int
            or not 1 <= value["from_step"] <= len(previous_steps)
            or type(value["field"]) is not str):
        raise HandoffError("invalid_model_plan")
    source = previous_steps[value["from_step"] - 1]
    if value["field"] not in ALLOWED_NUMERIC_FIELDS.get(source["tool"], ()):
        raise HandoffError("invalid_model_plan")
    return value


def validate_calculator_handoffs(args, previous_steps):
    """Validate plan-time structure; never resolve a result before execution."""
    if type(args) is not dict or set(args) != {"a", "b", "operation"}:
        raise HandoffError("invalid_model_plan")
    preview = {"operation": args["operation"]}
    for operand in ("a", "b"):
        value = args[operand]
        if type(value) is str:
            preview[operand] = value
        else:
            _binding(value, previous_steps)
            preview[operand] = "1"
    try:
        calculate(preview)  # Existing numeric, precision and operation limits.
    except (AgentError, TypeError, ValueError):
        raise HandoffError("invalid_model_plan") from None


def _triage_count(raw, source, field):
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, UnicodeError):
        raise HandoffError("plan_handoff_invalid_source") from None
    if (type(data) is not dict
            or data.get("source") != "GITHUB_PUBLIC_ISSUE_TRIAGE_READ_ONLY"
            or data.get("method") != "DETERMINISTIC_LABEL_RULES_V1"
            or data.get("repository") != source["args"]["repo"]
            or data.get("external_write_count") != 0
            or type(data.get("issues")) is not list):
        raise HandoffError("plan_handoff_invalid_source")
    counts = [data.get(k) for k in (
        "returned_count", "review_flagged_count", "unassessed_count")]
    if (any(type(n) is not int for n in counts)
            or not 0 <= counts[0] <= MAX_ISSUES
            or not 0 <= counts[1] <= counts[0]
            or not 0 <= counts[2] <= counts[0]
            or counts[1] + counts[2] != counts[0]
            or len(data["issues"]) != counts[0]
            or any(type(item) is not dict
                   or item.get("priority_signal") not in
                   ("REVIEW_FLAGGED", "UNASSESSED")
                   for item in data["issues"])
            or sum(item["priority_signal"] == "REVIEW_FLAGGED"
                   for item in data["issues"]) != counts[1]):
        raise HandoffError("plan_handoff_invalid_source")
    return str(data[field])


def resolve_calculator_handoffs(step, previous_steps, previous_results):
    """Return (concrete calculator args, immutable audit entry or None).

    Fail before the next durable IN_FLIGHT transition if a saved result is
    absent, corrupt, from the wrong tool, out-of-bounds or zero denominator.
    """
    if type(previous_results) is not list or len(previous_results) != len(previous_steps):
        raise HandoffError("plan_handoff_invalid_source")
    try:
        validate_calculator_handoffs(step["args"], previous_steps)
    except (KeyError, TypeError, HandoffError):
        raise HandoffError("plan_handoff_invalid_source") from None
    resolved = {"operation": step["args"]["operation"]}
    refs = []
    for operand in ("a", "b"):
        value = step["args"][operand]
        if type(value) is str:
            resolved[operand] = value
            continue
        index = value["from_step"] - 1
        source_step, result = previous_steps[index], previous_results[index]
        if (type(result) is not dict or set(result) != {"tool", "output"}
                or result["tool"] != source_step["tool"]
                or type(result["output"]) is not str):
            raise HandoffError("plan_handoff_invalid_source")
        raw = result["output"]
        if source_step["tool"] == "calculate":
            numeric = raw
        elif source_step["tool"] == "github_issue_triage":
            numeric = _triage_count(raw, source_step, value["field"])
        else:
            raise HandoffError("plan_handoff_invalid_source")
        resolved[operand] = numeric
        refs.append({
            "operand": operand,
            "from_step": value["from_step"],
            "source_tool": source_step["tool"],
            "field": value["field"],
            "value": numeric,
            "source_sha256": sha256(raw.encode("utf-8")).hexdigest()
        })
    try:
        calculate(resolved)
    except (AgentError, TypeError, ValueError):
        raise HandoffError("plan_handoff_invalid_source") from None
    entry = {"step_index": len(previous_steps) + 1,
             "resolved_args": resolved,
             "bindings": refs} if refs else None
    return resolved, entry


def validate_handoff_history(history, steps, results):
    """Recompute exact lineage from durable results; detect corrupted audit log."""
    if type(history) is not list or type(steps) is not list or type(results) is not list:
        raise HandoffError("plan_handoff_log_invalid")
    expected = []
    for index in range(len(results)):
        step = steps[index]
        if step["tool"] != "calculate":
            continue
        try:
            _, entry = resolve_calculator_handoffs(
                step, steps[:index], results[:index])
        except HandoffError:
            raise HandoffError("plan_handoff_log_invalid") from None
        if entry is not None:
            expected.append(entry)
    if history != expected:
        raise HandoffError("plan_handoff_log_invalid")
    return expected
