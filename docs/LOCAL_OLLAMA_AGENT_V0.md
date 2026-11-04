# Slice 47: Optional local-model agent, tool permissions v0

The project remains an **open-source, single-operator, localhost-first prototype**, not SaaS. This slice is the first **actual inference and model-requested tool execution** path. Unlike the deterministic local workflow core and five mocks, this path genuinely connects to an independently installed Ollama model, but **only when the operator explicitly starts a CLI command**.

## Quick start

1. Install Ollama from its official distribution, download a tool-call-capable model and start Ollama on its default local endpoint. Models and memory requirements depend on the specific model and host; downloading them is not done by this repository.
2. From the project root, use a terminal with stdin piped to a prompt (or type and finish with EOF):

```powershell
"Berapa 128 dikali 17? Gunakan tool bila perlu." | python app/local_ollama_agent.py --model qwen2.5:3b
```

The shown model name is an example, **not** a tested model on the user's computer. If Ollama or that model is missing, the command fails without contacting an external provider. The CLI reads at most 3,002 characters and accepts 1–3,000 nonblank prompt characters. Prompts are **not** stored by this code. Do not send secrets or real customer PII into the prototype.

## Contract

- Calls only `http://127.0.0.1:11434/api/chat` using standard-library HTTP with OS proxy bypassed and a 25-second per-request timeout. No cloud provider credentials or subscription.
- Uses Ollama's chat tools protocol with two explicitly coded functions: `calculate(a,b,operation)` using bounded decimal arithmetic, and `utc_now()`. No filesystem access, shell, browser, email, CRM, GitHub mutations, credential access, outbound network tool, or extensible plugin loader.
- Each invocation has a hard maximum of four model turns and at most two tool requests per turn. Tool calls are validated again by code even if the model requests an unauthorized action. Model tool suggestions are not authorization.
- Rejects malformed messages, excessive reply sizes, unknown tool names, invalid calculator values, model names containing path/shell syntax and unsupported arguments. No tool side effects; `external_dispatch_count=0` refers to **business/tool dispatch**; the loopback Ollama request itself is actual local HTTP traffic.
- The result's model response is **untrusted generated text** and can be incorrect. The agent cannot verify external facts or claim it performed a business operation. Chat history is transient for this one CLI invocation, not persistent memory.
- Unit tests mock only the model transport and verify the real orchestration/permission code. They **do not prove** a local Ollama model has been installed, follows the tools protocol, or responds well in every environment.

## Known gaps

The CLI is not yet wired into the authenticated browser workbench, persistent workflow executor, human approvals or real external connectors. There is no provider abstraction, model capability discovery, model-resource management, durable queue, policy-audited local prompt storage, malware isolation or protection against a malicious Ollama process. The system running this script must trust its own installed local model server.

**Next recommended step:** authenticated browser UI for opt-in local inference with clear operator consent, error handling, and test fixtures; then a separate policy gate before any write-capable external tools. Do not silently promote the CLI to an autonomous external sender.
