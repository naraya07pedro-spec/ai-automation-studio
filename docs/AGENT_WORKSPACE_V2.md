# Agent Workspace v2 · Real owner lifecycle and explicit cloud inference

This increment extends the merged /workspace page. Backend permission and billing contracts remain unchanged.

## Agent Profiles
- Browse saved Agent identities; edit name/category/description/instructions/allowed read-only tools without silently discarding previously configured tool names.
- Profile update is optimistic-locking with expected_revision and reauthentication with the current owner password. Earlier bound plans are invalidated by the backend.
- Disabling is explicitly confirmed and owner-password protected, cannot be undone by the existing API, and does NOT delete scoped memory.
- UI creation still grants only calculate/utc_now. Editing can retain or remove existing extra tools, but does not offer arbitrary new grants.

## Project/Agent scoped notes
- List actual scoped notes including expired records and revocation metadata, editing via expected_revision, new notes and explicit live deletion. Owner password per mutation, scope pinning and CAS performed server-side.
- Explicit 7/30/90/365 day retention on writes, maximum 600 character note text. Editing imported data deliberately reclassifies it as an owner note with an explicit warning.
- Expired notes cannot be updated; deleting from the live ledger does not eliminate backup/swap copies. Selected note IDs remain limited to three and are rechecked for policy/revision at approval/execution.

## Cloud text provider selection
- Distinct Ask cloud AI mode lists only server-enabled OpenAI Responses, Anthropic Messages or Gemini generateContent backends.
- Requires owner-chosen provider/model, current password, unchecked-by-default per-request paid consent, confirmation, cryptographically random unique request ID.
- No auto fallback, retries, scheduled tasks or paid provider keys in browser. Receipt ID is shown for read-only recovery; failure is treated as unconfirmed and user must inspect provider charges and saved receipt before another independent request.
- The existing five-attempts/day limit and output-token caps are **not** a financial budget limit. UI does not guess pricing, model support or account readiness.

## Verification
- Existing browser fixture runs real Agent/Profile/memory/plan SQLite and synthetic provider adapter, with real HTTP session/CSRF/password gates. This is not real provider billing or device acceptance.
- Browser test exercises Agent create/update/disable, memory create/edit revocation against approved plan, and one explicit cloud request + read-only receipt recovery + logout secret clearing.
- Existing Python and Chromium workflows must pass on the exact PR head, and owner-device credentials/backup/VPS remain unverified.
