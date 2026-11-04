#!/usr/bin/env python3
"""On-demand, live (NOT stubbed) read-only GitHub connector verification.

The default probe reads public metadata from octocat/Hello-World using the
*exact production adapter*, checks the HTTP response and sanitization path,
and reports only counts and bounded status codes, never title/body/identity.

No tokens, repositories writes, real customer data, saved SQLite or pollers.
A temporary in-memory local import can be checked *only* with an explicit
CLI switch and only if genuinely eligible labeled issues are returned.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from github_public_issue_intake import PublicGitHubReadOnlyIntake
from local_csv_batch import LocalCSVBatchIntake
from local_workflow_core import LocalWorkflowCore

PROBE_ID = "public_probe_flow"


def probe_public_github(repo, *, require_eligible=False,
                        check_ephemeral_import=False, fetcher=None):
    """Return (exit_code, redacted proof). No provider payload escapes."""
    safe_repo = PublicGitHubReadOnlyIntake._check_repo(repo)
    if safe_repo is None:
        return 2, {"status": "INVALID_PUBLIC_REPOSITORY",
                   "live_transport": "NOT_CHECKED"}
    core = LocalWorkflowCore(":memory:")
    try:
        created, _ = core.create({
            "workflow_id": PROBE_ID,
            "display_name": "Public Probe Rules",
            "service_filter": "any",
            "min_budget_tier": 1,
            "followup_policy": "review_required",
        })
        if created != 201:
            return 2, {"status": "LOCAL_PROBE_SETUP_FAILED",
                       "live_transport": "NOT_CHECKED"}
        activated, _ = core.activate({
            "workflow_id": PROBE_ID, "expected_version": 1})
        if activated != 200:
            return 2, {"status": "LOCAL_PROBE_SETUP_FAILED",
                       "live_transport": "NOT_CHECKED"}
        intake = PublicGitHubReadOnlyIntake(
            core, LocalCSVBatchIntake(core), fetcher=fetcher)
        code, result = intake.preview({
            "workflow_id": PROBE_ID, "repo": safe_repo})
        if code != 200:
            return 1, {
                "status": "LIVE_READ_FAILED",
                "live_transport": "NOT_VERIFIED",
                "error_code": result.get("error", "provider_unavailable"),
            }
        summary = {
            "status": "LIVE_TRANSPORT_VERIFIED",
            "live_transport": "VERIFIED",
            "repository": safe_repo,
            "api": "PUBLIC_GITHUB_ISSUES_READ_ONLY",
            "scanned_first_page": result["scanned"],
            "ignored_first_page": result["ignored_count"],
            "eligible_first_page": result["new_count"] + result["replay_count"],
            "external_dispatch_count": 0,
            "local_storage": "IN_MEMORY_ONLY",
        }
        if result["status"] == "NO_MATCHING_PUBLIC_ISSUES":
            summary["label_mapping"] = "NO_ELIGIBLE_ISSUES_ON_FIRST_PAGE"
            summary["temporary_import"] = "NOT_ATTEMPTED"
            if require_eligible or check_ephemeral_import:
                summary["status"] = "LIVE_MAPPING_NOT_VERIFIED"
                return 3, summary
            return 0, summary
        summary["label_mapping"] = "VERIFIED_WITH_LIVE_LABELS"
        summary["temporary_import"] = "NOT_ATTEMPTED"
        if check_ephemeral_import:
            committed_code, committed = intake.commit({
                "workflow_id": PROBE_ID,
                "snapshot_id": result["snapshot_id"]})
            if committed_code != 200:
                summary["status"] = "TEMPORARY_LOCAL_IMPORT_FAILED"
                summary["temporary_import"] = "FAILED"
                return 4, summary
            history_code, history = core.history(PROBE_ID)
            if (history_code != 200
                    or history["total"] != committed["new_count"]
                    or committed["external_dispatch_count"] != 0):
                summary["status"] = "TEMPORARY_HISTORY_MISMATCH"
                summary["temporary_import"] = "FAILED"
                return 4, summary
            summary["temporary_import"] = "VERIFIED_IN_MEMORY_ONLY"
            summary["temporarily_written_event_count"] = history["total"]
        return 0, summary
    finally:
        core.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=(
        "Bounded real public GitHub API smoke via the shipped read-only adapter."))
    parser.add_argument("--repo", default="octocat/Hello-World",
                        help="Public GitHub owner/repo (never a URL or token).")
    parser.add_argument("--require-eligible", action="store_true",
                        help="Fail if no issue has all required aas:* labels.")
    parser.add_argument("--check-ephemeral-import", action="store_true",
                        help="After real preview, verify local commit in an "
                             "in-memory DB only (requires matching issues).")
    args = parser.parse_args(argv)
    code, summary = probe_public_github(
        args.repo,
        require_eligible=args.require_eligible,
        check_ephemeral_import=args.check_ephemeral_import)
    print(json.dumps(summary, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
