# Slice 74 · Owner-approved local Markdown artifact export

## Problem solved

Previously, a COMPLETED reviewed plan could synthesize an evidence-linked report, but the practical file output was only an explicit **browser download**. This slice adds a separately authorized **server-owned local write**, so a saved report can become a real, inspectable Markdown file in one predictable workspace without supplying a destination path to the model or browser.

## Opt-in and owner flow

Start the existing authenticated loopback workbench with:

```powershell
python app/local_server.py --enable-local-agent --enable-agent-plans --enable-plan-artifacts
```

1. Propose, review, approve and complete the existing read-only plan (one step at a time or by the separately confirmed server-owned batch).
2. Explicitly click **Generate final report**. This is still a separate bounded Ollama call. The report must exist and pass the existing exact-quote membership checks; interpretations remain **unverified**.
3. Click **Save .md into local workspace**. The browser displays a new native confirmation naming the exact file and warning that contents remain plaintext on this computer. Declining writes nothing.
4. The server uses **only its persisted plan and report** to construct Markdown. It never accepts user-supplied output path, filename, directory or document body on this route.
5. The result is a SHA-256 receipt and the fixed relative location `.ai-automation-studio/exports/local-agent-report-<plan-id>.md` below the **actual operating-system user home directory**. See the UI's receipt for the exact filename.

Existing browser **Copy report** and **Download .md** remain available and do not depend on this new optional feature.

## API and trust boundary

`POST /api/local-agent-plan-export`, JSON `{"plan_id":"<24 lowercase hex>"}`. Requires loopback Host/Origin restrictions, authenticated operator cookie, CSRF token, feature opt-in (`--enable-plan-artifacts`) and ready security audit. No GET write, background export, model action, or generic file write tool.

- The exporter revalidates the saved report against the exact selected step-output excerpt, rejects altered source quotes/extra schema fields and escapes Markdown syntax in untrusted AI/owner text to prevent executable links/images in the generated file. Exact quote membership does **not** prove model-generated conclusions true.
- Only the server-selected, fixed `exports` subdirectory is writable. Its directory and parent must not be symlinks; on POSIX the export directory must be owner-only. Windows ACL strength is not asserted by a synthetic CI pass.
- An exclusive temporary file is flushed and hard-linked to the target filename, then the temp is removed. An existing target is never replaced. An **identical** repeat returns `EXISTS_IDENTICAL`, with zero new writes; an altered/linked/symlink/nonregular target fails closed with `artifact_target_conflict`. No arbitrary filesystem traversal.
- Maximum single Markdown file size: **32,768 UTF-8 bytes**. File is intentionally **plaintext**; avoid confidential customer information, passwords, API keys, sensitive documents or private data.
- The dedicated security audit records `LOCAL_ARTIFACT_EXPORT` attempt and success without report bodies, source text or local paths. If its completion record fails after writing, API reports uncertain outcome; inspect the workspace before retrying. Repeated writes are never sent externally or silently retried.
- Exporting never calls a model, GitHub, cloud provider, messaging API, mail transport or shell. Local file write count is included in the export receipt.

## Limits and next capabilities

This is a **narrow local write action**, not a generic model-accessible filesystem tool. Saved output stays in the local export folder, not a user-chosen directory or cloud drive. File deletion, overwrite, encryption, independent factual verification, external integrations and powerful agent re-planning remain separate future milestones. The existing SQLite plan itself stores selected source snapshots in plaintext.

Synthetic CI tests permission gates, no export before completion/report, fail-closed path handling, symlink/nonregular targets, idempotency, malicious Markdown text escaping, headless Chromium confirmation and a disposable Windows runner filesystem. **No real owner-PC Ollama tests** are performed; live device acceptance remains deferred at [Issue #68](https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68).
