# Real local Automation Library v1

Route: `/automations`. This is a separate lightweight UI over the EXISTING `LocalWorkflowCore`, not a new worker or fabricated AI Agent.

## What is executable
- List actual saved local rules by category, status, version and recorded run count; service-category filtering.
- Create an explicit SQLite-backed DRAFT with exact service filter, minimum budget tier and review/draft-only policy.
- Activate one exact reviewed rule version after confirmation.
- Evaluate and save a pseudonymous event with exact `workflow_id`, `event_id`, `service_code`, `budget_tier` and `timeline`.
- Return the original saved receipt on exact same-ID+same-input replay and fail on changed input using an already recorded ID. Inspect up to 25 latest saved runs and total recorded count.
- Uses the existing same-origin owner session and CSRF on mutation; frontend uses textContent for untrusted text and never stores credentials or results in browser localStorage.

## Non-capabilities
- These saved rules only evaluate local qualification and review decisions. They DO NOT invoke the AI model, schedule a worker, send mail/WhatsApp, edit CRM, call webhooks or reach external sites. Owner has existing advanced controls and separately approved connectors for those functions.
- Local service and budget tiers are NOT financial provider spend caps. There is no price/real revenue evidence, even if a rule returns QUALIFIED_DRAFT_ONLY.
- Only 50 local rules, 500 events per rule and a 25-record latest event page are supported by the underlying core. Absence from latest page does not imply that a record never existed.
- No automatic event ID retry or external side-effect reconciliation. If network errors occur, inspect saved run history before any deliberate additional action.
- The older five-card synthetic mock gallery remains on the advanced root page and is NOT presented here as real workflows.

## Verification
- Python HTTP surface tests verify the new page loads as an owner-login shell while private workflow/history APIs are denied without authentication.
- Headless Chromium runs real `LocalWorkflowCore` SQLite fixture through authenticated HTTP to test draft, reviewed activation, original local event receipt, same-input replay, conflicting-input rejection, history and logout. Synthetic environment, NO owner device, live Ollama, paid provider or VPS proof.
- Preserve existing full 3.11–3.13 Python and Chromium regression workflows before merging.

## Owner access
Start the private Windows launcher from an updated checkout (existing feature flags are unchanged). Open `http://127.0.0.1:8765/automations` or choose Automations in the Agent Workspace sidebar. Use Advanced controls for scheduling and connector actions. Nothing in this incremental library relaxes authorization.
