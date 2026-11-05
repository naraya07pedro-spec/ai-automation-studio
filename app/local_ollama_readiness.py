"""Explicit real-local Ollama readiness verification, no mock model in production.

Uses fixed loopback /api/tags and one bounded /api/chat tool-loop probe.
Never stores user prompts, sends data to cloud services, or writes to apps.
"""
import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request

from local_ollama_agent import (AgentError, run_agent, safe_local_opener,
                                valid_model_name)

TAGS_URL = "http://127.0.0.1:11434/api/tags"
MAX_TAG_BYTES = 64 * 1024
MAX_INSTALLED_MODELS = 64
PROBE_PROMPT = ('This is a tool-calling compatibility check. Call the calculate '
                'tool with a="7", b="8" and operation="multiply". '
                'After you receive the tool response, reply briefly with DONE. '
                'Do not call any other tool.')


def fetch_local_tags():
    """Only fixed loopback, no environment proxies, no redirects."""
    try:
        with safe_local_opener().open(Request(TAGS_URL, method="GET"), timeout=3) as response:
            raw = response.read(MAX_TAG_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError):
        raise AgentError("local_model_unavailable") from None
    if len(raw) > MAX_TAG_BYTES:
        raise AgentError("model_catalog_too_large")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise AgentError("invalid_model_catalog") from None


def installed_models(fetcher=None):
    """Only bounded validated model names; never expose server metadata."""
    document = (fetcher or fetch_local_tags)()
    if type(document) is not dict or type(document.get("models")) is not list:
        raise AgentError("invalid_model_catalog")
    entries = document["models"]
    if len(entries) > MAX_INSTALLED_MODELS:
        raise AgentError("model_catalog_too_large")
    names = []
    for item in entries:
        if type(item) is not dict or not valid_model_name(item.get("name")):
            raise AgentError("invalid_model_catalog")
        names.append(item["name"])
    return sorted(set(names))


def probe_model(model, fetcher=None, transport=None):
    """User-requested inference; verify model completion and observed tool protocol.

    A direct text answer proves only that inference worked. It does NOT prove
    tool calling; do not promote it to a verified tools-capable model.
    """
    if not valid_model_name(model):
        raise AgentError("invalid_model_name")
    names = installed_models(fetcher)
    if model not in names:
        raise AgentError("model_not_installed")
    result = run_agent(PROBE_PROMPT, model, transport=transport)
    tools = result.get("tools_used")
    if type(tools) is not list:
        raise AgentError("invalid_model_probe_result")
    observed = "calculate" in tools
    return {
        "status": "MODEL_RESPONDED",
        "model": model,
        "tool_calling": "OBSERVED" if observed else "NOT_OBSERVED",
        "answer_present": bool(result.get("answer")),
        "external_dispatch_count": 0,
        "history_saved": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Probe installed Ollama models on fixed localhost without cloud access")
    parser.add_argument("--model", help="Explicit installed model to probe for tool calling")
    args = parser.parse_args(argv)
    try:
        if args.model:
            result = probe_model(args.model)
        else:
            result = {"status": "OLLAMA_REACHABLE",
                      "models": installed_models(), "external_dispatch_count": 0}
    except AgentError as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc),
                          "external_dispatch_count": 0}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
