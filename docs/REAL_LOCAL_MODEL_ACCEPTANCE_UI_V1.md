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
