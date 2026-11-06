"""Explicit ON-HOST live Ollama scheduled-inference acceptance, never a CI success.

This script uses the actual fixed-loopback Ollama model catalog and production
run_agent, with a completely disposable SQLite schedule and model checkpoint.
It waits for a real wall-clock minute slot and verifies persisted result after
reopening the database. No user-owned Studio state is read or changed.

Synthetic acceptance is exposed ONLY by run_disposable_schedule, which always
labels its result SYNTHETIC and cannot be presented as live runtime evidence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_model_safety import LocalModelSafetyState, ModelSafetyError
from local_ollama_agent import run_agent, AgentError, valid_model_name
from local_ollama_readiness import installed_models
from local_readonly_agent_scheduler import (
    LocalReadOnlyAgentSchedules, AgentScheduleError)

PROMPT = ("This is a private local Ollama scheduler acceptance check. "
          "Respond with a short sentence using only your own reasoning. "
          "No file, network, email, shell or external business action is requested.")
PROBE_WINDOW_SECONDS = 200
WAIT_INTERVAL_SECONDS = 0.25


class LiveAcceptanceError(Exception):
    pass


def studio_port_in_use():
    """Fail closed: isolated smoke does NOT participate in a running Studio's gate."""
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=0.4):
            return True
    except ConnectionRefusedError:
        return False
    except (TimeoutError, OSError):
        # An ambiguous network state must not grant a contention exemption.
        return True


def _validate_persisted_schedule(before, after, row):
    if (len(before) != 1 or len(after) != 1
            or before[0] != after[0]
            or before[0]["status"] != "COMPLETED"
            or type(before[0]["answer"]) is not str
            or not 1 <= len(before[0]["answer"].strip()) <= 6000
            or any(x not in ("utc_now", "calculate")
                   for x in before[0]["tools_used"])
            or before[0]["external_dispatch_count"] != 0
            or row["attempted_runs"] != 1 or row["max_runs"] != 1
            or row["status"] != "EXHAUSTED"):
        raise LiveAcceptanceError("persisted_schedule_invariant_failed")
    return {
        "scheduled_slot_utc": before[0]["scheduled_for_utc"],
        "answer_present": True,
        "answer_sha256": hashlib.sha256(
            before[0]["answer"].encode("utf-8")).hexdigest(),
        "answer_length": len(before[0]["answer"]),
        "tools_used": before[0]["tools_used"],
        "attempted_runs": 1,
        "external_dispatch_count": 0,
        "sqlite_reopen_verified": True,
        "no_replay_on_reopen": True,
    }


def run_disposable_schedule(model, *, runner, clock=None,
                            deadline_seconds=PROBE_WINDOW_SECONDS,
                            sleep=WAIT_INTERVAL_SECONDS,
                            after_start=None):
    """Low-level acceptance exercise. ALWAYS synthetic-labelled.

    Test injection of clock or runner is allowed only here; do not use this
    function to assert real Ollama or real Tencent deployment.
    """
    if not valid_model_name(model):
        raise LiveAcceptanceError("invalid_model_name")
    if (type(deadline_seconds) not in (int, float)
            or not 1 <= deadline_seconds <= PROBE_WINDOW_SECONDS
            or type(sleep) not in (int, float)
            or not 0.05 <= sleep <= 2):
        raise LiveAcceptanceError("invalid_probe_limits")
    clock_args = {"clock": clock} if clock is not None else {}
    with tempfile.TemporaryDirectory(prefix="aas-disposable-model-acceptance-") as tmp:
        base = Path(tmp)
        model_checkpoint = LocalModelSafetyState(base / "model-checkpoint.sqlite3")
        gate = threading.BoundedSemaphore(1)
        gate_lock = threading.Lock()
        db_path = base / "ai-schedules.sqlite3"
        engine = None
        try:
            engine = LocalReadOnlyAgentSchedules(
                db_path, runner=runner, model_safety=model_checkpoint,
                agent_gate=gate, agent_state_lock=gate_lock, **clock_args)
            created = engine.create({
                "prompt": PROMPT, "model": model,
                "cadence": {"cadence": "minutes", "every": 1,
                            "timezone": "Asia/Jakarta"},
                "max_runs": 1, "expires_days": 1})
            tid = created["task_id"]
            if engine.history(tid)["runs"] or created["status"] != "ACTIVE":
                raise LiveAcceptanceError("schedule_not_created_in_pending_state")
            engine.start()
            if after_start is not None:
                after_start(engine, created)
            begin = time.monotonic()
            receipt = None
            while time.monotonic() - begin < deadline_seconds:
                history = engine.history(tid)["runs"]
                if history and history[0]["status"] != "IN_FLIGHT":
                    receipt = history[0]
                    break
                time.sleep(sleep)
            if receipt is None:
                raise LiveAcceptanceError("no_completed_real_clock_slot_within_probe_limit")
            if receipt["status"] != "COMPLETED":
                raise LiveAcceptanceError("scheduled_inference_not_completed_" +
                                          receipt["status"].lower())
            before = engine.history(tid)["runs"]
            original = engine.list()["tasks"][0]
            if model_checkpoint.phase() != "IDLE":
                raise LiveAcceptanceError("model_checkpoint_not_idle_after_completion")
            engine.close()
            engine = None
            # A second instance must read the exact same durable receipt,
            # without automatically running the inference a second time.
            replay_blocker = lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("model_replayed_after_restart"))
            restored = LocalReadOnlyAgentSchedules(
                db_path, runner=replay_blocker, model_safety=model_checkpoint,
                agent_gate=gate, agent_state_lock=gate_lock, **clock_args)
            engine = restored
            after = restored.history(tid)["runs"]
            if restored.run_due() != 0:
                raise LiveAcceptanceError("duplicate_inference_claim_after_reopen")
            safe = _validate_persisted_schedule(before, after, original)
            if model_checkpoint.phase() != "IDLE":
                raise LiveAcceptanceError("model_checkpoint_not_idle_after_reopen")
            return {
                "status": "SYNTHETIC_DISPOSABLE_SCHEDULER_PASSED",
                "inference_source": "CALLER_INJECTED_NOT_LIVE_EVIDENCE",
                "model": model,
                "task_id": tid,
                "max_inference_attempts": 1,
                "checks": {
                    "scheduled_worker_started": True,
                    "model_response_saved": True,
                    "safe_model_checkpoint": True,
                    "sqlite_restart_readback": True,
                    "duplicate_after_restart_blocked": True,
                    "no_external_actions": True,
                },
                **safe,
            }
        finally:
            if engine is not None:
                engine.close()
            model_checkpoint.close()


def live_acceptance(model):
    """Only live entrypoint. No injectable catalog or transport, no mocks."""
    if not valid_model_name(model):
        raise LiveAcceptanceError("invalid_model_name")
    if studio_port_in_use():
        raise LiveAcceptanceError("studio_port_8765_in_use_stop_app_before_isolated_probe")
    names = installed_models()
    if model not in names:
        raise LiveAcceptanceError("model_not_installed_locally")
    # Capture a harmless local probe duration; do not expose model text in
    # the persisted redacted report.
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    elapsed = time.monotonic()
    print("LIVE LOCAL CHECK: waiting for an actual due slot and Ollama response.",
          file=sys.stderr)
    result = run_disposable_schedule(model, runner=run_agent)
    if result["status"] != "SYNTHETIC_DISPOSABLE_SCHEDULER_PASSED":
        raise LiveAcceptanceError("disposable_schedule_did_not_pass")
    return {
        **result,
        "status": "LIVE_LOCAL_OLLAMA_SCHEDULE_ACCEPTANCE_PASSED",
        "inference_source": "REAL_LOCAL_OLLAMA_127_0_0_1_11434",
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.monotonic() - elapsed, 2),
        "actual_server_8765_used": False,
        "tencent_instance_identity_verified": False,
        "service_24h_or_reboot_recovery_verified": False,
        "actual_user_task_executed": False,
        "no_user_app_state_changed": True,
        "limitations": "Actual installed Ollama model on this host plus isolated SQLite worker only; neither the existing Studio process, Tencent Cloud identity, 24/7 uptime nor external provider completion is proved.",
    }


def save_redacted_evidence(report):
    """Owner-only, create-once redacted evidence; never write inside Git checkout."""
    if (type(report) is not dict
            or report.get("status") != "LIVE_LOCAL_OLLAMA_SCHEDULE_ACCEPTANCE_PASSED"
            or report.get("inference_source") != "REAL_LOCAL_OLLAMA_127_0_0_1_11434"
            or not re.fullmatch(r"[0-9a-f]{24}", str(report.get("task_id", "")))):
        raise LiveAcceptanceError("only_genuine_live_reports_may_be_saved")
    owner_root = Path.home() / ".ai-automation-studio"
    if owner_root.is_symlink():
        raise LiveAcceptanceError("unsafe_evidence_directory")
    folder = owner_root / "live-acceptance"
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise LiveAcceptanceError("unsafe_evidence_directory")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.is_symlink():
        raise LiveAcceptanceError("unsafe_evidence_directory")
    if os.name == "posix":
        os.chmod(folder, 0o700)
    filename = "live-local-ollama-" + report["task_id"] + ".json"
    path = folder / filename
    if path.is_symlink():
        raise LiveAcceptanceError("unsafe_evidence_path")
    data = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except (OSError, FileExistsError):
        raise LiveAcceptanceError("cannot_create_unique_evidence_file") from None
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Explicit live ON-HOST local Ollama scheduled inference and reboot-independent SQLite readback acceptance")
    parser.add_argument("--model", required=True, help="Exact locally installed Ollama model; no automatic download")
    parser.add_argument("--save-redacted-evidence", action="store_true",
                        help="Create an owner-local JSON evidence record outside Git; never store model answer")
    args = parser.parse_args(argv)
    try:
        report = live_acceptance(args.model)
        if args.save_redacted_evidence:
            save_redacted_evidence(report)
        report["redacted_evidence_saved"] = bool(args.save_redacted_evidence)
    except (AgentError, ModelSafetyError, AgentScheduleError, LiveAcceptanceError,
            OSError, sqlite3.Error) as exc:
        reason = (str(exc) if isinstance(exc, LiveAcceptanceError)
                  else str(exc) if isinstance(exc, AgentError)
                  else "live_local_acceptance_unavailable")
        print(json.dumps({"status": "NOT_VERIFIED", "error": reason,
                          "live_model_verified": False,
                          "tencent_instance_identity_verified": False,
                          "external_dispatch_count": 0}, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
