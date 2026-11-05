"""Operator-initiated real Ollama + approved public GitHub triage smoke.

No service startup, no cloud model, no external writes, no persistent plan DB.
Requires installed local model, an existing owner grant, and typed confirmation.
"""
import argparse
import json
from pathlib import Path
import sys

from local_agent_github_reader import (AllowedGitHubIssuesReader,
                                       GitHubPlanReadError, valid_repo)
from local_agent_task_plans import (LocalAgentPlanStore, PlanError,
                                    propose_readonly_plan)
from local_ollama_agent import AgentError, valid_model_name
from local_ollama_readiness import installed_models
from local_source_policy import LocalPublicSourcePolicy, SourcePolicyError


def smoke(model, repo, *, policy=None, model_catalog=None, plan_proposer=None,
          reader=None, confirm=None):
    """Run the same production plan state transitions in ephemeral local SQLite.

    Only one exact github_issue_triage step matching the requested repository is
    accepted. The user must type an exact confirmation before the provider GET.
    """
    canonical = valid_repo(repo)
    if canonical is None or not valid_model_name(model):
        raise PlanError("invalid_smoke_arguments")
    policy = policy if policy is not None else LocalPublicSourcePolicy(
        Path.home() / ".ai-automation-studio" / "public-github-sources.json")
    reader = reader if reader is not None else AllowedGitHubIssuesReader(policy)
    reader.check(canonical)
    names = (model_catalog or installed_models)()
    if model not in names:
        raise AgentError("model_not_installed")
    goal = ("Use only github_issue_triage to categorize open public issues from "
            + canonical + ". Return exactly one JSON step with tool github_issue_triage "
            "and args containing that exact repository.")
    proposal = (plan_proposer or
                (lambda request, model_name: propose_readonly_plan(
                    request, model_name, allow_github=True)))(goal, model)
    steps = proposal.get("steps") if type(proposal) is dict else None
    if (type(steps) is not list or len(steps) != 1
            or type(steps[0]) is not dict
            or steps[0].get("tool") != "github_issue_triage"
            or steps[0].get("args") != {"repo": canonical}
            or proposal.get("model") != model):
        raise PlanError("smoke_model_plan_not_compatible")
    store = LocalAgentPlanStore(":memory:", github_reader=reader)
    try:
        proposed = store.create(proposal)
        approval_phrase = "EXECUTE " + canonical
        response = (confirm or input)(
            "Plan is validated but NOT executed. To allow exactly one public "
            "GitHub GET, type '" + approval_phrase + "': ")
        if response != approval_phrase:
            store.cancel(proposed["plan_id"])
            raise PlanError("smoke_not_approved")
        approved = store.approve(proposed["plan_id"])
        if approved["status"] != "APPROVED":
            raise PlanError("smoke_approval_failed")
        done = store.advance(proposed["plan_id"])
        if done["status"] != "COMPLETED" or len(done["results"]) != 1:
            raise PlanError("smoke_execution_incomplete")
        result = json.loads(done["results"][0]["output"])
        if (result.get("source") != "GITHUB_PUBLIC_ISSUE_TRIAGE_READ_ONLY"
                or result.get("repository") != canonical
                or result.get("external_read_count") != 1
                or result.get("external_write_count") != 0):
            raise PlanError("smoke_provenance_invalid")
        return {
            "status": "REAL_LOCAL_MODEL_AND_PUBLIC_GITHUB_TRIAGE_EXECUTED",
            "model": model,
            "repository": canonical,
            "model_plan_proposed": True,
            "human_approved": True,
            "step_status": done["status"],
            "result": result,
            "plan_storage": "ephemeral_memory_only",
        }
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Opt-in real local Ollama + approved GitHub issue triage smoke")
    parser.add_argument("--model", required=True, help="Exact name in local Ollama catalog")
    parser.add_argument("--repo", required=True, help="Public owner/repo already on local allowlist")
    args = parser.parse_args(argv)
    try:
        outcome = smoke(args.model, args.repo)
    except (AgentError, PlanError, GitHubPlanReadError, SourcePolicyError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc),
                          "external_write_count": 0}))
        return 1
    except (OSError, EOFError, KeyboardInterrupt):
        print(json.dumps({"status": "BLOCKED", "error": "smoke_interrupted",
                          "external_write_count": 0}))
        return 1
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
