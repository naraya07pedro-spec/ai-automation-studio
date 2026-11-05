# Slice 59 · One-click real Ollama acceptance on the operator's local computer

AI Automation Studio remains **local-first, open source and single-operator**, not SaaS. The earlier multi-document and retrieval slices passed deterministic GitHub CI, but the user's **installed Windows Ollama model** has never been exercised by that CI. Slice 59 makes the crucial real-model gate directly accessible from the authenticated local web UI instead of asking the operator to manually run an unfamiliar smoke script.

## Run one actual local-model check

From the current checkout on the operator's computer, start Ollama separately and install a JSON-output-capable model that fits the device. **Neither this repository nor the web UI downloads a model, changes system settings or uses cloud AI.**

Start the existing localhost server with the multi-document feature explicitly enabled:

```powershell
python app/local_server.py --enable-local-agent --enable-multi-documents
```

Open `http://127.0.0.1:8765`, log in as the provisioned local owner, click **Find installed models** in the local-agent panel, and enter/select the exact installed model name. In the separate **Verify the installed model on this computer** section, click **Run real Ollama acceptance on this PC**.

That click is the operator's explicit consent for a **single live local model inference**. The server validates the selected model against the real Ollama `GET http://127.0.0.1:11434/api/tags` catalog, reads no user files, and sends only two harmless built-in text examples plus a fixed question to local `/api/chat` over loopback. It then requires a structured model answer containing **18:30 WIB**, together with an independently verified exact quotation of that fact from the original `schedule.md` sample (not a quote fabricated from `policy.txt`). This checks basic format adherence and source binding; it does **not** establish that all natural-language answers are accurate.

The result is displayed in the UI with model name, UTC completion time, elapsed duration, source citation, selected passage count, source mechanism, and available system RAM snapshots on Windows. Available system RAM before/after is **not** this model's RSS, peak memory, GPU utilization, or memory causation. A failed inference or unsupported model produces **BLOCKED**, with no synthetic success fallback.

You can still run the standalone CLI acceptance:

```powershell
python app/local_ollama_readiness.py
python app/local_multi_doc_smoke.py --model YOUR_INSTALLED_MODEL
```

The model name above is a placeholder, not an assumption that a model has already been installed.

## Crucial runtime-vs-test distinction

In the **shipping runtime**, the HTTP handler receives the production `smoke` function with no injected catalog or transport. Only a successful fixed-localhost Ollama request can produce:

```text
status: REAL_LOCAL_MULTI_DOCUMENT_SMOKE_PASSED
inference_source: LIVE_LOCAL_OLLAMA
```

In deterministic CI, tests deliberately inject model/catalog fixtures. The smoke tool now **must** label their output differently:

```text
status: SYNTHETIC_MODEL_SMOKE_PASSED
inference_source: INJECTED_TEST_TRANSPORT
```

The browser explicitly displays **SYNTHETIC FIXTURE: NOT REAL OLLAMA** when given an injected result and must never claim that this reflects the user's actual machine. This distinction corrects the previous smoke fixture where an injected model could inherit a misleading `REAL_...` status. A synthetic success is useful for verifying API/UI wiring only.

The real evidence must originate from a user-triggered call to the installed model **on that same local host** and be inspected there. GitHub CI has no access to the user's Windows PC and cannot produce those measurements. **No real-device success is claimed until that user-side action happens.**

## Security and operational boundaries

The device acceptance endpoint is `POST /api/local-agent-device-acceptance` with JSON `{"model":"installed:model"}`. It is available only when the server is started with both `--enable-local-agent` and `--enable-multi-documents`; it shares the existing single-inference semaphore with agent chat, document analysis and task plans. Every POST requires an authenticated owner session, same-origin and CSRF. Unsupported body schemas, model identifiers or models not listed in Ollama are blocked before running the challenge. No browsing, external writes, GitHub reads, arbitrary file access, persisted evaluation history, password inspection, model downloads or background monitoring occur.

The browser displays output as inert text, clears the displayed result when the model changes or on logout, and labels an in-flight call without preemptively claiming success. Clearing the browser does **not** cancel an already-running local inference or guarantee erasure from Ollama process memory or Windows swap. For an 8-GB-class device, model loading and inference may be slow or fail under the transport timeout, and the test makes no guaranteed performance claim. Do not run it simultaneously with large memory-intensive applications.

## Verification

Backend tests exercise owner login, origin/CSRF denial, disabled-by-default behavior, strict model shape, absent installed model, global model gate and verified source evidence. They also assert that an injected fake model is **never** described as a real Windows model, and reject a superficially correct quote paired with a wrong answer. Chromium browser tests click the actual button against a synthetic injected model and inspect the explicit fixture warning and logout cleanup.

**Next release gate:** 7/7 GitHub Actions checks on the final PR head, followed by squash merge and progress recording. A successful merge means the user can initiate real device testing in the local UI; it does **not** mean that test has already been completed on their machine.

## Slice 60 runtime reliability and diagnosis update (2026-11-05)

On machines with little free RAM, a model can take longer to load on its **first** inference. The user-triggered **device acceptance** now allows a maximum of **90 seconds for its single Ollama HTTP request**, instead of inheriting the ordinary chat transport's **25-second** bound. Ordinary chat, agent planning, document Q&A, and other model calls still use the existing default 25 seconds. The acceptance test does not add automatic retry, background polling, model installation, restart, or memory optimization.

The fixed local transport still sends **only** to `http://127.0.0.1:11434/api/chat` with no proxies or redirects, bounds response bytes and exposes stable diagnostic error codes rather than raw Ollama responses:

| Error code | Meaning and next action |
| --- | --- |
| `local_model_timeout` | One request exceeded the configured bound or the local endpoint reported a timeout. Check Ollama responsiveness and running model; a request might still run on the backend after the client gives up. Do not immediately spam retries. |
| `local_model_unavailable` | The local connection failed, including refused connections or redirects. Ensure Ollama is running and the localhost endpoint is reachable. |
| `model_not_installed` | The exact selected model is not in the local `/api/tags` catalog. Use **Find installed models**. |
| `local_model_endpoint_missing` | Ollama returned HTTP 404 for chat. Verify installation, API compatibility and selected model. |
| `local_model_server_error` | Ollama returned a server error (HTTP 5xx other than 504). Check its own logs, resource availability and model; this does **not** prove an out-of-memory condition. |
| `invalid_model_response` / `invalid_multi_document_model_response` | Response was not valid JSON or did not satisfy the expected output schema. A different compatible local model may be required. |
| `smoke_source_evidence_not_verified` / `invalid_multi_document_citation` | The answer or verified citation did not match the fixed challenge. The model did not meet this evidence requirement. |
| `model_response_too_large` | Output exceeded the existing bounded response limit. |
| `local_agent_busy` | Another inference currently owns the local model gate. |

The browser displays the sanitized error plus a concrete follow-up action. **There is no automatic second POST** after an HTTP timeout, because the local model may still be generating and a second request could raise CPU/RAM load further. The 90-second setting is a socket request timeout, not a guaranteed hard deadline for a backend process or a forced termination. Model-specific peak RAM remains unmeasured. None of these changes proves a real installed model was run on the user's Windows device; that requires an operator-side run.

The reliability gate is covered with injected timeout, HTTP 404/500/504, refused connection, oversized/malformed output, model/answer mismatch, and browser diagnostic tests on multiple Python versions and Chromium. CI cannot measure real machine RAM or local Ollama behavior.
