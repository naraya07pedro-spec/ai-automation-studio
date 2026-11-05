# Slice 61 · Ollama host preflight and explicit real-device evidence

AI Automation Studio is an open-source, single-owner **localhost workbench**, not SaaS. This slice makes the missing operational evidence actionable: on the operator's own Windows PC, inspect basic host and Ollama prerequisites *without invoking any model*, then separately choose whether to run a real, bounded inference challenge. Neither a GitHub CI fixture nor a successful preflight proves that a real Windows model works.

## Quick checks from Windows

Pull the current `main` branch and use the Python interpreter for this project:

```powershell
git pull
python app/local_windows_preflight.py
```

The default command does **not** execute an inference or load model weights. It reads only the fixed localhost `127.0.0.1:11434/api/tags` installed-model catalog and a Windows system-wide RAM snapshot. It prints structured JSON with an actionable status and no login tokens, personal file paths, process lists, or application document content.

Expected statuses are `OLLAMA_NOT_READY` (API unreachable/malformed), `NO_MODELS_INSTALLED`, `MODEL_SELECTION_REQUIRED`, `MODEL_NOT_INSTALLED`, or `READY_FOR_EXPLICIT_SMOKE`. Model names are validated and capped; no model is installed automatically. A model may appear in the catalog and still fail inference or not fit available memory. System RAM snapshots are **not model footprint, RAM required, free GPU memory, or proof of compatibility**. A snapshot below 1024 MB available generates a warning, not a diagnosis of out-of-memory.

To check the intended model's installation without running inference:

```powershell
python app/local_windows_preflight.py --model YOUR_INSTALLED_MODEL
```

To explicitly run **one actual Ollama inference** on two harmless built-in text samples, with the same strict source/answer acceptance criteria as previous slices:

```powershell
python app/local_windows_preflight.py --model YOUR_INSTALLED_MODEL --run-smoke
```

The `--run-smoke` option is the deliberate operator action. It requires an exact installed model selected via local Ollama's catalog and invokes the existing local acceptance function with its one-request **90-second** timeout (there is **no automatic retry**). It does not upload anything, inspect personal files, download models, write report files, or modify system settings. The command reports `REAL_DEVICE_SMOKE_PASSED` only if the selected model actually completed a bounded loopback inference, answered **18:30 WIB** correctly and cited that exact source text in `schedule.md`. Failures produce `DEVICE_SMOKE_BLOCKED` with a sanitized reason and suggested next action. A failed live attempt is still marked as attempted, and no failed attempt is reported as a pass.

This is a **single-task compatibility check**, not proof of model quality, long-term stability or suitability for all autonomous agent tasks. Run it on the actual target Windows host to establish evidence for that host.

## Optional read-only button in the browser

Start the existing local server with the pre-existing flags:

```powershell
python app/local_server.py --enable-local-agent --enable-multi-documents
```

Log in as the local owner at `http://127.0.0.1:8765`, then use **Check Windows & Ollama readiness (no inference)**. The button calls owner-authenticated read-only `GET /api/local-agent-device-preflight` with no query parameters. It displays host OS, Python version, whether the local Ollama API is reachable, installed model names, available/total system RAM, warnings, and next actions. **It never triggers a model inference.** This endpoint is disabled when the local agent/multi-document opt-in is off; it does not require CSRF because it is a read-only authenticated GET.

Only after inspecting preflight should the user select an installed model and explicitly click **Run real Ollama acceptance on this PC**, which is a separate owner-authorized, CSRF-protected POST. No personal document is read in either check. Reset/logout clears the browser's displayed preflight information; it does not erase system/Ollama logs or cancel work already in flight. Do not publish system observations externally unless you choose to share them.

## Interpretation and failure boundaries

| Status | Meaning | Next step |
| --- | --- | --- |
| `OLLAMA_NOT_READY` | Local catalog not reachable or invalid | Start/repair Ollama on the PC; verify port 11434 |
| `NO_MODELS_INSTALLED` | API reachable but no installed model found | Manually install a model appropriate for the machine |
| `MODEL_SELECTION_REQUIRED` | Model catalog reachable, none selected | Choose an exact installed model name |
| `MODEL_NOT_INSTALLED` | Selected model absent from catalog | Select a name from the actual returned list |
| `READY_FOR_EXPLICIT_SMOKE` | Basic host/model checks pass | Invoke real acceptance if you want model evidence |
| `REAL_DEVICE_SMOKE_PASSED` | Real loopback inference and source check passed on the host running the command | Record model/result/time/RAM separately as observed evidence |
| `DEVICE_SMOKE_BLOCKED` | Inference attempted but reported a categorized error | Troubleshoot actual code, without assuming RAM is the cause |

**CI boundary:** Python and browser tests inject synthetic catalogs, RAM and models, and verify all these state transitions. The CI runner is **not the user's Windows PC**. The tests do not install or exercise a real Ollama model; their successes are engineering validation only. The already-existing Windows PowerShell CI job checks ACL behavior on disposable CI files, not Ollama hardware.

**Data minimization:** JSON output includes only model names, OS type, Python version, coarse system RAM snapshot, sanitized status and (only on explicit smoke) the fixed, nonsensitive source quotation, timing and memory summary. No OS user profile, network adapters, private file names, email addresses, device serials or secrets. Fixed local HTTP; no proxies, redirects, external writes, or default model execution.

**Next decision after this slice:** obtain **one actual** preflight + live acceptance result from the operator's PC. If unreachable, diagnose Ollama installation. If model absent, select an installed option. If low available RAM, investigate the actual workload and feasibility. If JSON/quote validation fails, change/adjust the local model after verifying the model's real output. Until that evidence arrives, do not represent this as a verified production deployment.
