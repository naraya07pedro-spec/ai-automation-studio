"""Non-destructive localhost Ollama/Windows preflight, plus explicit live acceptance.

No directories, private files, model downloads or cloud URLs are visited. The
default command does NOT run inference. stdout is intentionally redacted JSON
that is useful for sharing when diagnosing a real Windows installation.
"""
import argparse
from datetime import datetime, timezone
import json
import platform
import sys

from local_multi_doc_smoke import smoke, windows_memory_snapshot
from local_ollama_agent import AgentError, valid_model_name
from local_ollama_readiness import installed_models
from local_document_analysis import LocalDocumentError

_GUIDANCE = {
    "local_model_unavailable": "Start Ollama and confirm its local API at 127.0.0.1:11434.",
    "invalid_model_catalog": "Ollama returned a malformed installed-model catalog.",
    "model_catalog_too_large": "The local model catalog exceeded its size limit.",
    "model_not_installed": "Select an exact installed model name; no model is downloaded.",
    "local_model_timeout": "The model request timed out. Do not retry immediately; check whether Ollama is still running.",
    "local_model_endpoint_missing": "Inspect the local Ollama API and the selected model name.",
    "local_model_server_error": "Inspect local Ollama logs. This error alone does not establish RAM exhaustion.",
    "smoke_source_evidence_not_verified": "The model did not answer and cite the fixed evidence correctly.",
    "invalid_multi_document_model_response": "The model JSON format does not satisfy the strict citation schema.",
    "invalid_multi_document_citation": "The model cited text absent from its selected source.",
    "local_multi_document_model_unavailable": "Local model inference was unavailable; inspect Ollama.",
    "invalid_model_response": "The local endpoint returned invalid JSON.",
    "model_response_too_large": "The model response exceeded the fixed output limit.",
}


def _safe_ram(snapshot):
    """Accept only bounded integer summaries, never process names or paths."""
    if type(snapshot) is not dict:
        return None
    result = {}
    for key in ("total_ram_mb", "available_ram_mb", "memory_load_pct"):
        value = snapshot.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            result[key] = value
    return result if len(result) == 3 else None


def preflight(model=None, *, catalog=None, memory=None, system=None):
    """No model inference. Fixed localhost model catalog + system-wide RAM only."""
    if model is not None and not valid_model_name(model):
        raise AgentError("invalid_model_name")
    runtime = system if system is not None else platform.system()
    memory_probe = memory if memory is not None else windows_memory_snapshot
    try:
        ram = _safe_ram(memory_probe())
    except (OSError, AttributeError, ValueError):
        ram = None
    report = {
        "status": "PRECHECK_INCOMPLETE",
        "source": "LOCAL_OLLAMA_HOST_PREFLIGHT",
        "host_os": runtime if runtime in ("Windows", "Linux", "Darwin") else "OTHER",
        "python_version": "{}.{}.{}".format(*sys.version_info[:3]),
        "selected_model": model,
        "ollama_reachable": False,
        "installed_models": [],
        "installed_model_count": 0,
        "selected_model_installed": False,
        "system_ram": ram,
        "memory_note": (
            "System-wide available RAM is a snapshot, not model RSS or peak. "
            "A low available value alone does not prove model incompatibility."
        ),
        "live_model_inference_attempted": False,
        "application_files_read": False,
        "external_write_count": 0,
        "history_saved": False,
    }
    try:
        names = (catalog or installed_models)()
    except AgentError as exc:
        error = str(exc)
        if error not in ("local_model_unavailable", "invalid_model_catalog",
                         "model_catalog_too_large"):
            error = "local_model_unavailable"
        report.update(status="OLLAMA_NOT_READY",error=error,
                      next_action=_GUIDANCE[error])
        return report
    # The production catalog already validates and bounds the list. Injected
    # callbacks used by tests must meet the same contract.
    if (type(names) is not list or len(names) > 64 or
            any(not valid_model_name(n) for n in names)):
        report.update(status="OLLAMA_NOT_READY",
                      error="invalid_model_catalog",
                      next_action=_GUIDANCE["invalid_model_catalog"])
        return report
    names = sorted(set(names))
    report.update(ollama_reachable=True,installed_models=names,
                  installed_model_count=len(names))
    if not names:
        report.update(status="NO_MODELS_INSTALLED",
                      next_action="Install a suitable local Ollama model manually; this tool never downloads one.")
    elif model is None:
        report.update(status="MODEL_SELECTION_REQUIRED",
                      next_action="Choose an installed model by exact name, then initiate a separate real-model check.")
    elif model not in names:
        report.update(status="MODEL_NOT_INSTALLED",error="model_not_installed",
                      next_action=_GUIDANCE["model_not_installed"])
    else:
        report.update(status="READY_FOR_EXPLICIT_SMOKE",
                      selected_model_installed=True,
                      next_action="Run the explicit live Ollama check; this preflight is not inference evidence.")
    if ram is not None and ram["available_ram_mb"] < 1024:
        report["resource_warning"] = (
            "Less than 1024 MB system RAM available at this instant; "
            "close memory-heavy apps before model inference. This is not a model-specific requirement."
        )
    return report


def diagnose(model=None, *, run_smoke=False, catalog=None, memory=None,
             system=None, live_smoke=None):
    """Opt-in inference only, and only with a model confirmed by local tags."""
    report = preflight(model, catalog=catalog, memory=memory, system=system)
    if not run_smoke:
        return report
    if report["status"] != "READY_FOR_EXPLICIT_SMOKE":
        report["smoke_status"] = "NOT_ATTEMPTED"
        return report
    # Set before dispatch: an inference that fails has still been attempted.
    report["live_model_inference_attempted"] = live_smoke is None
    try:
        result = (live_smoke or smoke)(model)
        # Do not trust an injected callback to pretend to be live.
        if (type(result) is not dict
                or result.get("model") != model
                or result.get("source_evidence_verified") is not True
                or result.get("external_write_count") != 0
                or result.get("history_saved") is not False
                or (live_smoke is None
                    and (result.get("status") != "REAL_LOCAL_MULTI_DOCUMENT_SMOKE_PASSED"
                         or result.get("inference_source") != "LIVE_LOCAL_OLLAMA"))):
            raise LocalDocumentError("invalid_device_acceptance_result")
    except (AgentError, LocalDocumentError) as exc:
        error = str(exc)
        if error not in _GUIDANCE:
            error = "local_model_unavailable"
        report.update(status="DEVICE_SMOKE_BLOCKED",smoke_status="BLOCKED",
                      error=error,next_action=_GUIDANCE[error])
        return report
    except Exception:
        report.update(status="DEVICE_SMOKE_BLOCKED",smoke_status="BLOCKED",
                      error="local_model_unavailable",
                      next_action=_GUIDANCE["local_model_unavailable"])
        return report
    report.update(
        status=("REAL_DEVICE_SMOKE_PASSED" if live_smoke is None
                else "SYNTHETIC_DEVICE_SMOKE_PASSED"),
        smoke_status=result["status"],
        inference_source=result.get("inference_source"),
        smoke_result={
            "model":result["model"],
            "elapsed_ms":result.get("elapsed_ms"),
            "source_evidence_verified":True,
            "system_ram_available_before_mb":result.get("available_ram_before_mb"),
            "system_ram_available_after_mb":result.get("available_ram_after_mb"),
            "citations":result.get("citations",[]),
            "external_write_count":0,
        },
        live_model_inference_attempted=(live_smoke is None))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(
        description="Non-invasive host Ollama check; --run-smoke explicitly executes one live inference.")
    parser.add_argument("--model",help="Exact name from the installed-model catalog")
    parser.add_argument("--run-smoke",action="store_true",
                        help="Explicitly run one real local model challenge using harmless built-in text")
    args=parser.parse_args(argv)
    if args.run_smoke and not args.model:
        parser.error("--run-smoke requires --model")
    try:
        report=diagnose(args.model,run_smoke=args.run_smoke)
    except AgentError as exc:
        report={"status":"INVALID_INPUT","error":str(exc),
                "external_write_count":0,"live_model_inference_attempted":False}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["status"] in (
        "MODEL_SELECTION_REQUIRED","READY_FOR_EXPLICIT_SMOKE",
        "REAL_DEVICE_SMOKE_PASSED","SYNTHETIC_DEVICE_SMOKE_PASSED") else 1


if __name__=="__main__":
    raise SystemExit(main())
