# Windows quickstart · Paid provider opt-in and Workspace entry (Stage 133)

The Windows launcher `START_AI_STUDIO.cmd` now accepts paid Anthropic/Gemini backend flags without exposing secret values in arguments or the UI.

## Modes

- `--mode full` (default) runs local Ollama features and optionally public read-only connectors. It opens the new `/workspace` in the user's browser.
- `--mode local-ai` runs Ollama features without the extra public source reads; opens `/workspace`.
- `--mode cloud-only` runs WITHOUT local Ollama and requires at least ONE explicitly enabled remote provider. Opens `/workspace`. Only on-demand text requests are available for Anthropic/Gemini; no cloud plan generation is implied.
- `--mode offline` never enables paid Anthropic/Gemini provider flags and opens existing `/mission` with deterministic local workflows.

Supported opt-in flags:
`--enable-anthropic-on-demand` uses pre-provisioned private `AAS_ANTHROPIC_API_KEY`;
`--enable-gemini-on-demand` uses `AAS_GEMINI_API_KEY`;
existing `--enable-scheduled-openai` uses `AAS_OPENAI_API_KEY` and retains previous semantics, including its cloud-only route.

Examples (do NOT paste credentials into a terminal command or GitHub):

```powershell
.\START_AI_STUDIO.cmd --mode cloud-only --enable-anthropic-on-demand
.\START_AI_STUDIO.cmd --mode cloud-only --enable-gemini-on-demand
.\START_AI_STUDIO.cmd --mode full --enable-gemini-on-demand
```

The server refuses to start if enabled API credentials are missing or invalid in local startup checks. Startup alone does not prove provider validity, quota or billing readiness. Every paid ask still requires current owner password, per-request explicit consent, an immutable request ID, a model name, and is subject to the existing durable safety ledger. No automatic retries or provider fallback; the five-attempt UTC-day ceiling is not a hard currency budget.

CI contract tests verify exact CLI flags, offline denial and cloud-only no-Ollama configuration. No real owner Windows PC, external API billing or VPS deployment was tested.
