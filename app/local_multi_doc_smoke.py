"""Operator-run real Ollama multi-document smoke with honest Windows metrics.

Uses only two bundled non-sensitive, in-memory synthetic texts. No browser,
file system scan, downloaded model, or report files. Never invent success.
"""
import argparse
from datetime import datetime, timezone
import ctypes
import json
import os
import sys
import time

from local_document_analysis import LocalDocumentError
from local_multi_document_analysis import analyze_multiple_documents
from local_ollama_agent import AgentError, ollama_transport
from local_ollama_readiness import installed_models


EXAMPLE_DOCUMENTS = [
    {"filename": "policy.txt",
     "content": "Workflow ownership should be reviewed every month. "
                "The policy is maintained by the operations team."},
    {"filename": "schedule.md",
     "content": "Maintenance review meeting starts at 18:30 WIB every Thursday. "
                "Location: Room 4."},
]
EXAMPLE_QUESTION = "What time does the maintenance review meeting start?"


def windows_memory_snapshot():
    """Global available physical RAM, not this model's measured memory usage."""
    if os.name != "nt":
        return None
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    value = MemoryStatus()
    value.dwLength = ctypes.sizeof(value)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return None
    except (OSError, AttributeError):
        return None
    return {"total_ram_mb": round(value.ullTotalPhys / 1048576),
            "available_ram_mb": round(value.ullAvailPhys / 1048576),
            "memory_load_pct": value.dwMemoryLoad}


def smoke(model, *, catalog=None, transport=None, clock=None, memory=None):
    available = (catalog or installed_models)()
    if model not in available:
        raise AgentError("model_not_installed")
    now = clock or time.perf_counter
    memory_probe = memory or windows_memory_snapshot
    before = memory_probe()
    started = now()
    # Allow a 90s bounded first-load window on slow/8GB-class Windows PCs.
    # Normal agent/chat calls retain their existing 25s limit. No retry.
    effective_transport = (transport if transport is not None else
                           lambda payload: ollama_transport(payload, timeout_seconds=90))
    result = analyze_multiple_documents(
        EXAMPLE_DOCUMENTS, EXAMPLE_QUESTION, model, transport=effective_transport)
    elapsed = round((now() - started) * 1000)
    after = memory_probe()
    source_text = EXAMPLE_DOCUMENTS[1]["content"]
    citations = result.get("citations", [])
    verified = (type(citations) is list and any(
        type(c) is dict and c.get("filename") == "schedule.md"
        and type(c.get("start_char")) is int
        and type(c.get("end_char")) is int
        and 0 <= c["start_char"] < c["end_char"] <= len(source_text)
        and type(c.get("quote")) is str
        and "18:30 WIB" in c["quote"]
        and c["quote"] == source_text[c["start_char"]:c["end_char"]]
        and c.get("verified_exact_match") is True for c in citations))
    if (result.get("status") != "MULTI_DOCUMENT_ANALYZED_LOCAL"
            or result.get("model_calls") != 1
            or result.get("insufficient_evidence") is not False
            or type(result.get("answer")) is not str
            or "18:30 WIB" not in result["answer"]
            or not verified
            or result.get("external_write_count") != 0):
        raise LocalDocumentError("smoke_source_evidence_not_verified")
    actual_runtime = catalog is None and transport is None
    return {
        "status": ("REAL_LOCAL_MULTI_DOCUMENT_SMOKE_PASSED" if actual_runtime
                   else "SYNTHETIC_MODEL_SMOKE_PASSED"),
        "inference_source": ("LIVE_LOCAL_OLLAMA" if actual_runtime
                             else "INJECTED_TEST_TRANSPORT"),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "model_installed": True,
        "source_evidence_verified": True,
        "elapsed_ms": elapsed,
        "available_ram_before_mb": (before or {}).get("available_ram_mb"),
        "available_ram_after_mb": (after or {}).get("available_ram_mb"),
        "memory_note": "System-wide available physical RAM, NOT model RSS or peak RAM.",
        "model_calls": result["model_calls"],
        "model_request_timeout_seconds": (90 if actual_runtime else None),
        "selected_passages": result["selected_passages"],
        "source_files": result["selected_files"],
        "citations": result["citations"],
        "external_write_count": 0,
        "history_saved": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check a real installed Ollama model on two harmless local text samples")
    parser.add_argument("--model", required=True, help="Exact name from local Ollama /api/tags")
    args = parser.parse_args(argv)
    try:
        result = smoke(args.model)
    except (LocalDocumentError, AgentError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc),
                          "history_saved": False, "external_write_count": 0}))
        return 1
    except (OSError, KeyboardInterrupt):
        print(json.dumps({"status": "BLOCKED", "error": "smoke_interrupted",
                          "history_saved": False, "external_write_count": 0}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
