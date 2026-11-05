# Slice 72 · Explicit one-click run of remaining reviewed read-only steps

## Why this exists
Until Slice 71 the owner had to manually click **Execute next step** for every step, even after reviewing and approving a bounded, read-only 1–3-step plan. This feature allows one *new explicit decision* to run the remaining reviewed steps in sequence. It does not authorize a model to select new tools or generate a revised plan.

## Owner flow
1. Start the local server with the existing `--enable-local-agent --enable-agent-plans` flags and any separately needed read-only source flags.
2. Propose and inspect the plan and its exact tool arguments, selected documents and memory references.
3. Click **Approve reviewed plan**. Nothing runs at approval.
4. Optionally click **Run remaining reviewed read-only steps**. The browser asks for a new confirmation listing every remaining step, its tool and arguments. Canceling confirmation executes none.
5. After accepting, the browser dispatches **one existing authenticated /api/local-agent-plan-next request at a time**, awaiting its saved result before dispatching the next. At most three originally reviewed steps are possible.
6. Click **Stop after current step** to prevent *future* dispatches. A step already sent to the server may still complete. If a request fails, permissions change, the model times out, the page logs out, the plan state changes or the browser closes, no additional step is intentionally dispatched. Reload saved plan before any manual continuation.
7. After COMPLETED, the final evidence-linked deliverable still requires a separate explicit click; there is no automatic report generation.

## Security and execution contract
- The browser batch guard uses a deliberately closed tool allowlist: `calculate`, `utc_now`, `github_public_issues`, `github_issue_triage`, `github_issue_analysis`, `local_document_question`, `local_multi_document_question`.
- The allowlist is **not** a new permission grant. The existing server independently checks the owner session, CSRF, source grants/approval epoch, memory revision/digest, document snapshot integrity, handoff source integrity, Ollama safety checkpoint and plan lifecycle on **every** next-step request.
- The batch is an *in-page sequential interaction*, **not** a background worker, auto-resume, scheduled task, service-side batch API, model-proposed tool loop or permission to invoke arbitrary tools.
- Existing one-step mode is preserved. Nothing automatically starts after page reload, interrupted task recovery, logout, proposal or approval.
- A stop is **not a cancellation guarantee for the in-flight request**. A transport failure leaves an uncertain outcome until the saved plan is reloaded; the UI does not silently retry.
- In case new write-capable tools are introduced in future, they must remain excluded from this feature until independently designed and approved. Browser allowlisting is a defense-in-depth UX guard, not a replacement for backend authorization.

## Verification scope
- The Chromium synthetic-fixture regression checks that batch controls remain disabled before approval; dismissing the confirmation executes nothing; accepting the confirmation completes the exact existing two-step read-only plan and leaves the run controls disabled.
- GitHub CI verifies the code, test fixture and real browser interactions with **synthetic** Ollama responses, not real owner-device Windows/Ollama inference.
- Live local Ollama acceptance remains deferred at [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68), in accordance with the build-first decision.

## Remaining limitations
The run exists only while the browser page is open and authenticated. It is not an always-on universal AI agent. There is no shell access, arbitrary HTTP/folder access, remote side effects, scheduling, autonomous replanning or task recovery without explicit operator action. A browser stop cannot undo an already running tool, and document storage remains plaintext SQLite. Separate real-device testing and an eventual security review remain prerequisites for a stable release.
