"""Slice 76: synthetic reviewed CSV -> typed calculator handoffs."""
import json
from pathlib import Path
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_agent_task_plans import (
    LocalAgentPlanStore, PlanError, propose_readonly_plan, validate_steps)
from local_agent_batch_runner import LocalAgentBatchRunner
from local_agent_handoffs import resolve_calculator_handoffs, HandoffError
from local_csv_profile import parse_selected_csv, profile_selected_csv
from local_model_safety import LocalModelSafetyState

SOURCE = {"filename": "synthetic-ledger.csv", "content":
    "revenue,stage\n10,SYNTHETIC_RECORD_A\n20,SYNTHETIC_RECORD_B\n,SYNTHETIC_RECORD_C\n"}


def reference(field, column=None, step=1):
    result = {"from_step": step, "field": field}
    if column is not None:
        result["column"] = column
    return result


def profile():
    return {"tool": "local_csv_profile",
            "args": {"columns": ["revenue", "stage"]}}


def calculator(operand, operation="multiply", other="2"):
    return {"tool": "calculate",
            "args": {"a": operand, "b": other, "operation": operation}}


def plan(ref=None):
    return {"model": "fixture:model", "goal": "Profile the frozen CSV and double total",
            "steps": [profile(), calculator(
                ref if ref is not None else reference("column_sum", "revenue"))]}


class CSVHandoffUnitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "plans.sqlite3"
        self.store = LocalAgentPlanStore(self.path, csv_profiler=profile_selected_csv)
        self.addCleanup(self.store.close)

    def completed_one_step(self, source_plan=None):
        p = self.store.create(source_plan or plan(), csv_snapshot=SOURCE)
        self.store.approve(p["plan_id"])
        self.store.advance(p["plan_id"])
        return p["plan_id"]

    def test_column_sum_to_calculator_with_persisted_csv_and_output_hashes(self):
        pid = self.completed_one_step()
        before = self.store.get(pid)
        self.assertEqual("NEXT_READY", before["status"])
        self.assertEqual([], before["handoffs"])
        self.assertNotIn("SYNTHETIC_RECORD_", json.dumps(before))
        done = self.store.advance(pid)
        self.assertEqual("60", done["results"][1]["output"])
        self.assertEqual("COMPLETED", done["status"])
        binding = done["handoffs"][0]["bindings"][0]
        self.assertEqual("revenue", binding["column"])
        self.assertEqual("column_sum", binding["field"])
        self.assertEqual("30", binding["value"])
        self.assertEqual(done["csv_source"]["sha256"],
                         binding["source_csv_sha256"])
        self.assertEqual(64, len(binding["source_sha256"]))
        self.assertEqual("EXACT" if False else 1, len(done["handoffs"]))
        self.assertNotIn("SYNTHETIC_RECORD_", json.dumps(done))
        self.store.close()
        reloaded = LocalAgentPlanStore(self.path, csv_profiler=profile_selected_csv)
        try:
            self.assertEqual(done["handoffs"], reloaded.get(pid)["handoffs"])
        finally:
            reloaded.close()

    def test_all_supported_metrics_and_plain_numbers(self):
        cases = [
            ("row_count", None, "3"),
            ("column_sum", "revenue", "30"),
            ("column_nonempty_count", "revenue", "2"),
            ("column_missing_count", "revenue", "1"),
            ("column_distinct_count", "stage", "3"),
        ]
        parsed = parse_selected_csv(**SOURCE)
        raw = json.dumps(profile_selected_csv(parsed, ["revenue", "stage"]))
        for metric, column, expected in cases:
            with self.subTest(metric=metric):
                step = calculator(reference(metric, column))
                args, audit = resolve_calculator_handoffs(
                    step, [profile()], [{"tool": "local_csv_profile", "output": raw}],
                    csv_snapshot=parsed)
                self.assertEqual(expected, args["a"])
                self.assertEqual(expected, audit["bindings"][0]["value"])

    def test_invalid_plan_references_fail_before_storage(self):
        invalid = [
            reference("column_sum"),
            reference("row_count", "revenue"),
            reference("column_sum", "stage_ghost"),
            reference("column_mean", "revenue"),
            reference("column_distinct_count", "revenue", step=2),
            {"from_step": True, "field": "row_count"},
            {"from_step": 1, "field": "row_count", "path": "/etc/passwd"},
            {"from_step": 1, "field": "column_sum", "column": "../secret"},
            {"from_step": 1, "field": "column_sum", "column": "revenue",
             "metric": "mean"},
        ]
        for ref in invalid:
            with self.subTest(ref=ref):
                with self.assertRaises(PlanError):
                    validate_steps([profile(), calculator(ref)])
        with self.assertRaises(PlanError):
            validate_steps([calculator(reference("row_count")), profile()])
        with self.assertRaises(PlanError):
            validate_steps([{"tool":"utc_now","args":{}},
                            calculator(reference("row_count"))])

    def test_non_numeric_sum_blocks_before_calculation(self):
        pid = self.completed_one_step(plan(reference("column_sum", "stage")))
        with self.assertRaisesRegex(PlanError, "plan_handoff_invalid_source"):
            self.store.advance(pid)
        after = self.store.get(pid)
        self.assertEqual("NEXT_READY", after["status"])
        self.assertEqual(1, after["next_index"])
        self.assertEqual([], after["handoffs"])

    def test_modified_csv_output_rejected_without_second_step(self):
        pid = self.completed_one_step()
        saved = self.store.get(pid)
        forged = json.loads(saved["results"][0]["output"])
        forged["column_stats"][0]["numeric_summary"]["sum"] = "999"
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE agent_task_plans SET results_json=? "
                                  "WHERE plan_id=?", (json.dumps([{
                "tool": "local_csv_profile", "output": json.dumps(forged)}]), pid))
        with self.assertRaisesRegex(PlanError, "plan_handoff_invalid_source"):
            self.store.advance(pid)
        fresh = self.store.get(pid)
        self.assertEqual(1, fresh["next_index"])
        self.assertEqual([], fresh["handoffs"])

    def test_changed_csv_snapshot_is_detected_before_next_step(self):
        pid = self.completed_one_step()
        with self.store.lock, self.store.db:
            raw = self.store.db.execute("SELECT csv_json FROM agent_task_plans "
                                        "WHERE plan_id=?", (pid,)).fetchone()[0]
            self.store.db.execute("UPDATE agent_task_plans SET csv_json=? "
                                  "WHERE plan_id=?", (raw.replace("10,SYNTHETIC",
                                                                  "80,SYNTHETIC"), pid))
        with self.assertRaisesRegex(PlanError, "plan_storage_invalid"):
            self.store.get(pid)
        with self.assertRaisesRegex(PlanError, "plan_storage_invalid"):
            self.store.advance(pid)

    def test_corrupt_lineage_detected_after_finished_plan(self):
        pid = self.completed_one_step()
        self.store.advance(pid)
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE agent_task_plans SET handoff_log_json=? "
                                  "WHERE plan_id=?", (json.dumps([]), pid))
        with self.assertRaisesRegex(PlanError, "plan_storage_invalid"):
            self.store.get(pid)

    def test_revoked_csv_permission_prevents_handoff(self):
        pid = self.completed_one_step()
        self.store.csv_profiler = None
        with self.assertRaisesRegex(PlanError, "plan_csv_unavailable"):
            self.store.advance(pid)
        self.assertEqual("NEXT_READY", self.store.get(pid)["status"])

    def test_input_outside_calculator_limit_blocks_while_preserving_index(self):
        large = {"filename":"large.csv", "content":
                 "value\n999999999999\n999999999999\n"}
        p = self.store.create({"model":"fixture:model","goal":"Bounded arithmetic",
                               "steps":[{"tool":"local_csv_profile",
                                         "args":{"columns":["value"]}},
                                        calculator(reference("column_sum","value"))]},
                              csv_snapshot=large)
        self.store.approve(p["plan_id"])
        self.store.advance(p["plan_id"])
        with self.assertRaisesRegex(PlanError, "plan_handoff_invalid_source"):
            self.store.advance(p["plan_id"])
        self.assertEqual(1, self.store.get(p["plan_id"])["next_index"])

    def test_zero_division_is_rejected_before_dispatch(self):
        p = self.store.create({"model":"fixture:model","goal":"Do not divide by zero",
                               "steps":[profile(),
                                        {"tool":"calculate",
                                         "args":{"a":"100",
                                                 "b":reference("column_missing_count","stage"),
                                                 "operation":"divide"}}]},
                              csv_snapshot=SOURCE)
        self.store.approve(p["plan_id"])
        self.store.advance(p["plan_id"])
        with self.assertRaisesRegex(PlanError, "plan_handoff_invalid_source"):
            self.store.advance(p["plan_id"])
        self.assertEqual("NEXT_READY", self.store.get(p["plan_id"])["status"])

    def test_planner_metadata_only_and_no_model_execution(self):
        parsed = parse_selected_csv(**SOURCE)
        metadata = {"filename": parsed["filename"],
                    "source_bytes": len(parsed["content"].encode()),
                    "sha256": parsed["sha256"],"row_count":3,
                    "columns":parsed["headers"]}
        captured = []
        def transport(req):
            captured.append(req)
            return {"message":{"content":json.dumps({"steps":plan()["steps"]})}}
        proposed = propose_readonly_plan("Double CSV sum", "fixture:model",
                                          csv_context=metadata, transport=transport)
        self.assertEqual(plan()["steps"], proposed["steps"])
        self.assertEqual(1, len(captured))
        self.assertNotIn("SYNTHETIC_RECORD", json.dumps(captured))
        self.assertIn("column_sum", json.dumps(captured))
        self.assertEqual([], self.store.list())


class CSVHandoffServerBatchTests(unittest.TestCase):
    def test_server_worker_dispatches_checked_csv_then_calculator_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalAgentPlanStore(Path(tmp)/"plans.sqlite3",
                                         csv_profiler=profile_selected_csv)
            safety = LocalModelSafetyState()
            try:
                pid = store.create(plan(), csv_snapshot=SOURCE)["plan_id"]
                store.approve(pid)
                runner = LocalAgentBatchRunner(
                    store, safety, threading.BoundedSemaphore(1), threading.Lock())
                runner.start(pid)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    state = store.get(pid)
                    if state["batch_run_status"] == "COMPLETED":
                        break
                    time.sleep(0.02)
                self.assertEqual("COMPLETED", state["batch_run_status"])
                self.assertEqual("60", state["results"][1]["output"])
                self.assertEqual("30", state["handoffs"][0]["bindings"][0]["value"])
                self.assertEqual("IDLE", safety.phase())
            finally:
                store.close()
                safety.close()


if __name__ == "__main__":
    unittest.main()
