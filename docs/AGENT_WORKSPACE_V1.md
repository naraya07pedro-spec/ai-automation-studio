# Agent Workspace v1 · Visual redesign without backend replacement

## What this increment does
- A separate, responsive route: `http://127.0.0.1:8765/workspace`.
- Restrained, chat-first UI with sidebar, Agent library, model choice, context inspector, saved plans and review drawer.
- Uses **existing authenticated localhost APIs**, not simulated Agent profiles or client-side execution:
  - `GET /api/operator-session` and `POST /api/operator-login`, session cookie plus CSRF.
  - `GET /api/local-agent-capabilities`, `GET /api/local-agent-models`.
  - `GET /api/agent-profiles` and owner-password-confirmed `POST /api/agent-profile-create`.
  - `GET /api/agent-scoped-memory` for exactly selected project/Agent.
  - `POST /api/local-agent-plan-create`; owner-reviewed approve/next/cancel/resume; read-only persisted plan list/detail.
  - `POST /api/local-agent-run` for a **single local question**, clearly labeled as NOT Agent-bound or persistently conversational.
- Old `/mission`, `/`, `/jobs`, `/handoff` remain intact and accessible through navigation. There is no migration or service downtime.
- Single-step tool execution after owner review is the default. No implicit automatic work, provider call, external delivery, e-mail or scheduled action is introduced.

## Limits shown honestly
- Requires the actual server feature flags, installed Ollama model and owner password. It does **not** activate local inference or configure OpenAI/Anthropic/Gemini credentials.
- Named Agent identity currently constrains **local task plans**, not ad hoc local chat; the UI discloses that distinction.
- Memory notes are owner-curated/unverified and must be explicitly chosen (max 3) from the selected Agent and project. Empty selections are allowed for ordinary Agent plans; cross-Agent delegation still needs its separate stricter backend flow.
- New Agent creation exposes only two safe read-only tools in v1. Profile **updates**, disabling, memory creation/deletion, cross-Agent handoffs and advanced orchestration remain in authenticated server APIs or legacy operator tooling, not this visual slice.
- The Automation Library and Activity links go to existing routes. They are not reimplemented or claimed to be integrated into the new workspace.
- Owner password exists only in the password field long enough for authentication or one profile-creation request; cleared afterward. All data-bearing backend requests use same-origin session and CSRF, and all dynamic text uses `textContent`.
- A localhost HTML login screen is accessible before auth, while all Agent/profile/memory/plan API responses still require owner authorization. This is consistent with the existing `/mission` gate.
- The product is **not** proven operating on the owner Windows, VPS, live n8n/Gmail, or paid AI account by these tests.

## Verification
- `python -m unittest discover -s tests -p 'test_agent_workspace_surface.py' -v`
- `python -m unittest discover -s tests_browser -p 'test_workspace_browser.py' -v` (Playwright + Chromium, synthetic fixture).
- Existing full Python + Chromium CI should remain green before merge. Browser test saves desktop/mobile screenshots to `browser-artifacts/` for visual review.
- Manual acceptance: sign in, inspect capabilities and models, create an Agent profile (password confirmation), select the Agent and project notes, propose a read-only plan, verify its exact tool arguments, approve, run one step, reopen saved plan, sign out. Do not test with real personal or client secrets until owner-device storage/privacy boundaries are understood.

## Next design increment
Agent-specific execution timeline and memory management, then full responsive Agent Library categories and end-to-end owner-device acceptance. Do not add dozens of fake automation templates or silently wire an Agent to a paid provider.
