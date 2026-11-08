"""Explicit single-shot Anthropic Messages and Gemini generateContent text adapters.

No tools, search, external actions, proxy, redirect, fallback, retry, or secret
persistence. Only use after separate owner consent and durable local claim.
Official API shapes checked against provider references 2026-11-08.
"""
import json
import re
import ssl
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request

from local_ollama_agent import MAX_PROMPT
from openai_readonly_responses import (
    MAX_OUTPUT_TOKENS, MAX_REPLY_BYTES, MAX_ANSWER, RemoteInferenceError,
    validate_remote_api_key, safe_remote_opener,
)

ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
GEMINI_HOST = "https://generativelanguage.googleapis.com"
GEMINI_MODEL = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z", re.ASCII)
ANTHROPIC_MODEL = GEMINI_MODEL
ANTHROPIC_VERSION = "2023-06-01"
DEFINITE_REJECTIONS = frozenset((400, 401, 402, 403, 404, 422, 429))


def _validate(prompt, model, api_key, pattern):
    if type(prompt) is not str or not 1 <= len(prompt.strip()) <= MAX_PROMPT:
        raise RemoteInferenceError("remote_invalid_prompt")
    if type(model) is not str or pattern.fullmatch(model) is None:
        raise RemoteInferenceError("remote_invalid_model")
    validate_remote_api_key(api_key)


def _post_json(url, payload, headers, opener=None):
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=raw, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        **headers,
    })
    try:
        with (opener or safe_remote_opener()).open(request, timeout=35) as response:
            if getattr(response, "status", None) != 200:
                raise RemoteInferenceError("remote_response_uncertain")
            body = response.read(MAX_REPLY_BYTES + 1)
    except HTTPError as exc:
        if exc.code in DEFINITE_REJECTIONS:
            raise RemoteInferenceError("remote_provider_rejected") from None
        raise RemoteInferenceError("remote_response_uncertain") from None
    except (OSError, URLError, TimeoutError, ssl.SSLError, HTTPException, ValueError):
        raise RemoteInferenceError("remote_response_uncertain") from None
    if len(body) > MAX_REPLY_BYTES:
        raise RemoteInferenceError("remote_response_uncertain")
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise RemoteInferenceError("remote_response_uncertain") from None
    if type(data) is not dict:
        raise RemoteInferenceError("remote_response_uncertain")
    return data


def _read_usage(value, input_field, output_field):
    if type(value) is not dict:
        raise RemoteInferenceError("remote_response_uncertain")
    a, b = value.get(input_field), value.get(output_field)
    if (type(a) is not int or not 0 <= a <= 200000
            or type(b) is not int or not 0 <= b <= MAX_OUTPUT_TOKENS):
        raise RemoteInferenceError("remote_response_uncertain")
    return {"input_tokens": a, "output_tokens": b}


def _result(chunks, usage, mode):
    if (type(chunks) is not list or not 1 <= len(chunks) <= 12
            or any(type(t) is not str for t in chunks)):
        raise RemoteInferenceError("remote_response_uncertain")
    answer = "\n".join(chunks).strip()
    if not 1 <= len(answer) <= MAX_ANSWER:
        raise RemoteInferenceError("remote_response_uncertain")
    return {"mode": mode, "answer": answer, "usage": usage,
            "tools_used": [], "external_dispatch_count": 0}


def run_anthropic_readonly(prompt, model, *, api_key, opener=None):
    """Exactly one Anthropic Messages POST, default-deny provider tools."""
    _validate(prompt, model, api_key, ANTHROPIC_MODEL)
    data = _post_json(ANTHROPIC_ENDPOINT, {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }, {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
       opener=opener)
    if (data.get("type") != "message" or data.get("role") != "assistant"
            or data.get("stop_reason") != "end_turn"):
        raise RemoteInferenceError("remote_response_uncertain")
    contents = data.get("content")
    if (type(contents) is not list or not 1 <= len(contents) <= 12
            or any(type(c) is not dict or c.get("type") != "text"
                   or type(c.get("text")) is not str for c in contents)):
        raise RemoteInferenceError("remote_response_uncertain")
    return _result([c["text"] for c in contents],
                   _read_usage(data.get("usage"), "input_tokens", "output_tokens"),
                   "REMOTE_ANTHROPIC_READ_ONLY")


def run_gemini_readonly(prompt, model, *, api_key, opener=None):
    """Exactly one fixed-host Gemini generateContent POST, no tool declaration."""
    _validate(prompt, model, api_key, GEMINI_MODEL)
    data = _post_json(
        f"{GEMINI_HOST}/v1beta/models/{model}:generateContent",
        {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
         "generationConfig": {
             "maxOutputTokens": MAX_OUTPUT_TOKENS,
             "candidateCount": 1,
             "responseMimeType": "text/plain",
         }},
        {"x-goog-api-key": api_key}, opener=opener)
    candidates = data.get("candidates")
    if (type(candidates) is not list or len(candidates) != 1
            or type(candidates[0]) is not dict
            or candidates[0].get("finishReason") != "STOP"):
        raise RemoteInferenceError("remote_response_uncertain")
    content = candidates[0].get("content")
    if (type(content) is not dict or content.get("role") != "model"
            or type(content.get("parts")) is not list):
        raise RemoteInferenceError("remote_response_uncertain")
    parts = content["parts"]
    if (not 1 <= len(parts) <= 12
            or any(type(p) is not dict or set(p) != {"text"}
                   or type(p.get("text")) is not str for p in parts)):
        raise RemoteInferenceError("remote_response_uncertain")
    return _result(
        [p["text"] for p in parts],
        _read_usage(data.get("usageMetadata"),
                    "promptTokenCount", "candidatesTokenCount"),
        "REMOTE_GEMINI_READ_ONLY")


def create_anthropic_runner(api_key):
    secret = validate_remote_api_key(api_key)
    def runner(prompt, model):
        return run_anthropic_readonly(prompt, model, api_key=secret)
    return runner


def create_gemini_runner(api_key):
    secret = validate_remote_api_key(api_key)
    def runner(prompt, model):
        return run_gemini_readonly(prompt, model, api_key=secret)
    return runner
