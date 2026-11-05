# Slice 71 · Typed numeric cross-step handoffs

Owner-reviewed task plans can now use saved **numeric step output** in a later calculator step. This is deterministic orchestration, not an autonomous execution loop. The operator still clicks **Approve reviewed plan** and **Execute next step** once per step.

## Example: chain calculator steps

Run the existing local plan UI with:

    python app/local_server.py --enable-local-agent --enable-agent-plans

Ask: "Multiply 7 by 8, then double the stored result." The bounded planner can propose:

    {"steps":[
      {"tool":"calculate","args":{"a":"7","b":"8","operation":"multiply"}},
      {"tool":"calculate","args":{
        "a":{"from_step":1,"field":"output"},
        "b":"2","operation":"multiply"}}
    ]}

The first owner-clicked step records 56; the second owner-clicked step resolves exactly that saved value, validates it with the existing bounded calculator and records 112. No additional model inference is needed to resolve a reference.

## Example: permissioned public GitHub label proportions

With the separate GitHub flag and explicit owner grant:

    python app/local_server.py --enable-local-agent --enable-agent-plans --enable-agent-github-read

A three-step plan can (1) run the separately approved github_issue_triage for an owner-granted public repository, (2) divide its saved review_flagged_count by returned_count, and (3) multiply the saved calculator output by 100. All three steps require separate clicks. No current issue numbers are assumed. If returned_count is zero, the division fails closed before execution.

## Exact reference contract

Only the a or b operand of a calculate step can be a typed object with **exactly** the keys from_step and field. from_step must be a real integer, one-based, and **strictly precede** the current step in the same maximum-three-step plan.

| Previous step tool | Allowed numeric result fields |
| --- | --- |
| calculate | output |
| github_issue_triage | returned_count, review_flagged_count, unassessed_count |

Every other reference is rejected. There is no arbitrary JSONPath, template language, dynamic URL, data from document text or freeform AI answer, model-executed code, shell, or additional tool permissions. The model proposes typed references; the deterministic executor resolves only *already persisted* source results.

At resolution, the executor checks saved result ownership and type. For GitHub triage it checks the exact source marker, granted repository identity, deterministic method, zero external writes, counts within the 15-issue first-page limit, internal count consistency and visible per-issue label signals. Resulting scalars must pass the original Decimal calculator bounds, including finite numbers, up-to-24-character operands, maximum 10^12 input magnitude and nonzero divisor.

Missing, malformed, changed, nonnumeric or out-of-range source data fails as HTTP **409 plan_handoff_invalid_source** *before* the next step's IN_FLIGHT marker or any tool dispatch. Nothing automatically retries or substitutes guessed values. Completed prior steps are preserved and the owner can cancel or replan.

## Durable audit and previous plans

GitHub-derived counts are **not** a way to retain authority after a source grant is revoked. Before a later calculator uses any cached triage count, the runner rechecks the source repository and the **original approval epoch** with the separately granted public GitHub reader. Revocation, even if followed by regrant, denies this next step with the existing public_source_permission_changed conflict. A restart without that GitHub adapter also denies use of cached counts. No new GitHub fetch is needed for the permission recheck.

Each successful bound step stores a handoff record in a new backward-compatible SQLite column handoff_log_json (default empty array). The authenticated plan UI and final Markdown deliverable show destination step, original one-based source reference, concrete numeric input, source tool/field and SHA-256 of the exact saved source output. The original reviewed step definition remains intact. On reload, the store recomputes this lineage and rejects inconsistent audit records.

Older plans, approvals, and results migrate unchanged and have no handoffs. A restart never auto-executes the following step; interrupted steps still require explicit owner resumption. Stored plan and audit data are local plaintext SQLite, not suitable for secrets. Hash matching and numeric consistency are *not* independent verification of external source truth.

## Verification boundary

CI uses deterministic model/HTTP fixtures and real headless Chromium to validate planning structure, authorization/CSRF, manual steps, source integrity, restart/migration and report lineage. It does not establish real inference on the owner's Windows PC. Real device acceptance is deliberately deferred under GitHub Issue #68: https://github.com/naraya07pedro-spec/ai-automation-studio/issues/68

Related docs: LOCAL_AGENT_TASK_PLANS_V1.md, AGENT_ISSUE_TRIAGE_AND_E2E_V1.md, COMPLETED_TASK_DELIVERABLES_V1.md.
