"""Explicit owner-enabled OpenAI Responses inference for bounded PRIVATE schedules.

One fixed HTTPS API destination, single prompt, NO web/MCP/function/tools,
and no network proxy or redirects. Not a ChatGPT subscription integration.
No credentials in SQLite or HTTP response. Billing occurs at the provider;
per-task request caps are NOT a dollar cap or a provider billing limit.
"""
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from local_ollama_agent import AgentError, MAX_PROMPT

ENDPOINT = "https://api.openai.com/v1/responses"
MAX_OUTPUT_TOKENS = 512
MAX_REPLY_BYTES = 65536
MAX_ANSWER = 6000
MODEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$", re.ASCII)
API_KEY = re.compile(r"^[!-~]{20,512}$", re.ASCII)


class RemoteInferenceError(AgentError):
    """Stable sanitised reason; never include exception str or provider body."""


def valid_remote_model_name(name):
    return type(name) is str and bool(MODEL.fullmatch(name))


def validate_remote_api_key(value):
    if type(value) is not str or not API_KEY.fullmatch(value):
        raise RemoteInferenceError("remote_api_key_invalid")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_remote_opener():
    return build_opener(ProxyHandler({}), NoRedirect(),
                        HTTPSHandler(context=ssl.create_default_context()))


def _parse_response(payload):
    if (type(payload) is not dict or payload.get("object") != "response"
            or payload.get("status") != "completed"):
        raise RemoteInferenceError("remote_response_uncertain")
    output = payload.get("output")
    if type(output) is not list or len(output) > 12:
        raise RemoteInferenceError("remote_response_uncertain")
    chunks = []
    for item in output:
        if type(item) is not dict:
            raise RemoteInferenceError("remote_response_uncertain")
        if item.get("type") == "reasoning":
            continue
        if (item.get("type") != "message" or item.get("role") != "assistant"
                or item.get("status") not in (None, "completed")):
            raise RemoteInferenceError("remote_response_uncertain")
        contents = item.get("content")
        if type(contents) is not list or len(contents) > 12:
            raise RemoteInferenceError("remote_response_uncertain")
        for content in contents:
            if (type(content) is not dict
                    or content.get("type") != "output_text"
                    or type(content.get("text")) is not str):
                raise RemoteInferenceError("remote_response_uncertain")
            chunks.append(content["text"])
    answer = "\n".join(chunks).strip()
    if not 1 <= len(answer) <= MAX_ANSWER:
        raise RemoteInferenceError("remote_response_uncertain")
    usage = payload.get("usage")
    if type(usage) is not dict:
        raise RemoteInferenceError("remote_response_uncertain")
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if (type(input_tokens) is not int or type(output_tokens) is not int
            or not 0 <= input_tokens <= 200000
            or not 0 <= output_tokens <= MAX_OUTPUT_TOKENS):
        raise RemoteInferenceError("remote_response_uncertain")
    return {
        "answer": answer, "tools_used": [], "external_dispatch_count": 0,
        "mode": "REMOTE_OPENAI_READ_ONLY",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def run_openai_readonly(prompt, model, *, api_key, opener=None):
    """Exactly one billable POST attempt; no retries, no provider tool calls."""
    if type(prompt) is not str or not 1 <= len(prompt.strip()) <= MAX_PROMPT:
        raise RemoteInferenceError("remote_invalid_prompt")
    if not valid_remote_model_name(model):
        raise RemoteInferenceError("remote_invalid_model")
    validate_remote_api_key(api_key)
    payload = {
        "model": model,
        "input": [{"role": "user", "content": prompt}],
        "tools": [],
        "tool_choice": "none",
        "store": False,
        "background": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "truncation": "disabled",
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    request = Request(
        ENDPOINT, data=raw, method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    opener = opener or safe_remote_opener()
    try:
        with opener.open(request, timeout=35) as response:
            if getattr(response, "status", None) != 200:
                raise RemoteInferenceError("remote_response_uncertain")
            data = response.read(MAX_REPLY_BYTES+1)
    except HTTPError as exc:
        # A definitive authentication/quota/rate rejection is not retried,
        # preserving costs and preventing loops. Provider body is NEVER logged.
        if exc.code in (400, 401, 402, 403, 404, 422, 429):
            raise RemoteInferenceError("remote_provider_rejected") from None
        raise RemoteInferenceError("remote_response_uncertain") from None
    except (OSError, URLError, TimeoutError, ssl.SSLError, ValueError):
        raise RemoteInferenceError("remote_response_uncertain") from None
    if len(data) > MAX_REPLY_BYTES:
        raise RemoteInferenceError("remote_response_uncertain")
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise RemoteInferenceError("remote_response_uncertain") from None
    return _parse_response(decoded)


def create_openai_runner(api_key):
    """Binds a startup-only secret, never returned via HTTP/SQLite."""
    secret = validate_remote_api_key(api_key)
    def runner(prompt, model):
        return run_openai_readonly(prompt, model, api_key=secret)
    return runner
