# Slice 51 · Real local Ollama readiness and tool protocol verification

AI Automation Studio is an **open-source, user-controlled local agent**, not a subscription SaaS. This slice adds a human-triggered model check that uses the actual local Ollama HTTP endpoints rather than assuming green CI with synthetic model fixtures proves real-world model compatibility.

## Run a real check on your own computer

Install Ollama and a model capable of emitting Ollama tool calls separately; check that your model is already installed. The repository does **not** download model weights, auto-pull a model, or automatically run the test on server startup.

From the repository root:

```powershell
# Read locally installed model names (no model inference yet)
python app/local_ollama_readiness.py

# Replace this example with a real installed tool-capable model name
python app/local_ollama_readiness.py --model qwen2.5:3b
```

Or, after provisioning the local owner, start the UI:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-history
```

Log into `http://127.0.0.1:8765`, open **Ask your local AI agent**, then click **Find installed Ollama models** and **Test local model & tool calling**. The model name field supports suggestions from the installed catalog. `--enable-agent-history` is optional for readiness; `--enable-local-agent` is mandatory.

The test performs one **explicitly requested** read-only Ollama chat interaction with a constant synthetic prompt requesting calculator multiplication (7 × 8). It allows only the existing `calculate` and `utc_now` tool implementations; no shell, filesystem, email, CRM, or external business dispatch. It does not read saved conversations and does not store the test prompt or response in chat history.

## Interpreting results

| Response | Proven | Not proven |
| --- | --- | --- |
| `OLLAMA_REACHABLE` and model names | Local `/api/tags` replied and supplied sanitized installed models | Model inference, tool calling, or answer quality |
| `MODEL_RESPONDED` with `tool_calling: OBSERVED` | The installed model responded to `/api/chat` and the bounded agent executed at least one calculator tool call | Model reasoning quality, general tool reliability, external automation |
| `MODEL_RESPONDED` with `tool_calling: NOT_OBSERVED` | Actual local inference returned a text answer | Working tool calling; a capable model might ignore this particular prompt |
| `model_not_installed` | The selected name was not in the current installed catalog | Model compatibility |
| `local_model_unavailable` or other `BLOCKED` errors | A transport, model protocol, or validation problem occurred | A successful runtime |

**Important distinction:** `OBSERVED` is a successful end-to-end **tool protocol invocation through the existing bounded engine**, not a mathematical correctness certificate, a model evaluation suite, or an authority to execute real-world actions. This check is intentionally not persistent and does not provide multi-step autonomous job execution.

## Security and performance boundaries

- The production client calls only `http://127.0.0.1:11434/api/tags` for model names and `http://127.0.0.1:11434/api/chat` for test inference. Environment proxies and HTTP redirects are rejected for both; catalog responses are capped at 64 KiB, model catalog length at 64 names and generated model response at the engine's 128 KiB limit.
- Readiness APIs are unavailable unless the server is explicitly started with `--enable-local-agent`. Model discovery requires the current owner session; inference probe requires session, same-origin validation and CSRF. Probe shares the existing single-in-flight model request gate with regular chat.
- The probe can load model weights into RAM/GPU and consume local resources, depending on Ollama configuration and the model. This is **not** a negligible read-only operation in terms of compute, even though there are no business data writes. On machines with limited RAM, choose an appropriately small installed model.
- Local Ollama itself is a separate process with its own network/configuration. The repository cannot guarantee Ollama is bound to loopback, trustworthy, offline, or free of vulnerabilities. Do not expose this HTTP workbench via a public port or proxy.
- CI unit and Chromium tests run **injected protocol fixtures or disposable localhost test HTTP servers**, not a real downloaded Ollama model. A green CI result verifies the probe implementation, permissions and UI; it does not replace the manual command against an actually running installed model.

## Next acceptance gate

Before claiming real-model readiness for a Windows computer, record the **actual** command output on that computer, the exact model name/version, compatibility result, relevant hardware constraints and any resource or timeout failures. Only then expand toward bounded agent task planning and permissioned real-world tools.
