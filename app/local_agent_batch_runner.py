"""Bounded server-owned execution of one explicitly approved read-only plan.

The server thread survives browser navigation, NOT server process death. A SQLite
run-intent and per-step checkpoint make process death visible; no automatic replay.
"""
import threading

from local_agent_task_plans import PlanError
from local_model_safety import ModelSafetyError


class LocalAgentBatchRunner:
    def __init__(self, store, model_safety, gate, gate_lock):
        self.store = store
        self.model_safety = model_safety
        self.gate = gate
        self.gate_lock = gate_lock

    def start(self, plan_id):
        # Reserve the SAME gate used by interactive inference, before accepting
        # durable run intent. Only one model operation/batch can run at once.
        with self.gate_lock:
            if not self.gate.acquire(blocking=False):
                raise PlanError("local_agent_busy")
            try:
                accepted = self.store.begin_batch(plan_id)
            except BaseException:
                self.gate.release()
                raise
            try:
                threading.Thread(target=self._run, args=(plan_id,),
                                 name="reviewed-local-agent-batch",
                                 daemon=True).start()
            except BaseException:
                try:
                    self.store.finish_batch(plan_id, "FAILED")
                finally:
                    self.gate.release()
                raise PlanError("plan_batch_worker_unavailable") from None
            return accepted

    def stop(self, plan_id):
        # Does NOT wait for, abort, or retry an in-flight inference.
        return self.store.stop_batch(plan_id)

    def _run(self, plan_id):
        outcome = "FAILED"
        try:
            while True:
                snapshot = self.store.get(plan_id)
                if snapshot["batch_run_status"] == "STOPPING":
                    outcome = "STOPPED"
                    break
                if snapshot["batch_run_status"] != "RUNNING":
                    break
                if snapshot["status"] == "COMPLETED":
                    outcome = "COMPLETED"
                    break
                if snapshot["status"] not in ("APPROVED", "NEXT_READY"):
                    break
                started = False
                try:
                    # Durable model checkpoint for each step, even test callbacks.
                    self.model_safety.begin()
                    started = True
                    self.store.advance(plan_id, from_batch=True)
                except (PlanError, ModelSafetyError) as exc:
                    if str(exc) in ("local_model_timeout",
                                    "local_model_transport_uncertain"):
                        try:
                            self.model_safety.timeout()
                        except ModelSafetyError:
                            pass  # Fail closed; the safety backend remains untrusted.
                    break  # No retry, no next step after any failure.
                except Exception:
                    break  # No retry after an ambiguous unexpected failure.
                finally:
                    if started:
                        try:
                            self.model_safety.finish()
                        except ModelSafetyError:
                            # Preserve the uncertain checkpoint; never proceed.
                            break
            try:
                self.store.finish_batch(plan_id, outcome)
            except PlanError:
                pass  # Corrupt/unknown state remains review-required, never replayed.
        finally:
            self.gate.release()
