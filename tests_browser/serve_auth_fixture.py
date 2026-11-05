"""Browser CI fixture: authenticated loopback server with TEST-ONLY owner.

Production app/local_server.py never accepts an ephemeral/default password:
it requires offline one-time owner provisioning.
"""
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_operator_auth import LocalOperatorAuth
from local_source_policy import LocalPublicSourcePolicy
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore
from local_agent_conversation import LocalAgentConversation
from local_agent_task_plans import LocalAgentPlanStore
from local_agent_github_reader import AllowedGitHubIssuesReader
from local_ollama_agent import AgentError
from local_document_analysis import analyze_local_document
from local_multi_document_analysis import analyze_multiple_documents
from local_multi_doc_smoke import smoke as device_smoke
from local_agent_issue_analysis import analyze_public_issue_metadata
from local_server import create_handler


def main():
    home = tempfile.TemporaryDirectory()
    owner_path = Path(home.name) / "operator.json"
    LocalOperatorAuth.provision(owner_path, "ci_only_fixture_password_1234")
    owner = LocalOperatorAuth.load(owner_path)
    policy = LocalPublicSourcePolicy()
    # Only the test fixture grants a sample public repository on startup.
    # Shipping server starts with no grants.
    policy.grant("Octocat/Hello-World")
    audit = LocalSecurityAudit(Path(home.name) / "security-audit.sqlite3")
    core = LocalWorkflowCore(":memory:")
    conversation = LocalAgentConversation(":memory:")
    def fixture_github_fetch(repo):
        if repo != "Octocat/Hello-World":
            raise AssertionError("unexpected repository")
        return [{"state":"open","number":42,
                 "title":"Synthetic GitHub issue with human-readable title",
                 "labels":[{"name":"bug"},{"name":"priority:high"}]}]

    github_reader = AllowedGitHubIssuesReader(policy,fetcher=fixture_github_fetch)
    def fixture_analysis_transport(payload):
        assert payload["model"] == "fixture:model"
        assert "tools" not in payload
        return {"message": {"content": '{"items":[{"number":42,"observation":"Could the existing bug label warrant a reproduction check? Impact remains unknown."}]}' }}

    def fixture_issue_analyzer(reader, repo, epoch, model, goal):
        return analyze_public_issue_metadata(
            reader, repo, epoch, model, goal,
            transport=fixture_analysis_transport)

    plans = LocalAgentPlanStore(":memory:",github_reader=github_reader,
                                issue_analyzer=fixture_issue_analyzer)
    def fixture_planner(goal, model):
        if "analy" in goal.lower():
            return {"model": model, "goal": goal,
                    "steps": [{"tool": "github_issue_analysis",
                               "args": {"repo": "Octocat/Hello-World"}}]}
        if "triage" in goal.lower():
            return {"model": model, "goal": goal,
                    "steps": [{"tool": "github_issue_triage",
                               "args": {"repo": "Octocat/Hello-World"}}]}
        if "github" in goal.lower():
            return {"model": model, "goal": goal,
                    "steps": [{"tool": "github_public_issues",
                               "args": {"repo": "Octocat/Hello-World"}}]}
        return {"model": model, "goal": goal,
                "steps": [{"tool": "calculate",
                           "args": {"a": "7", "b": "8", "operation": "multiply"}},
                          {"tool": "utc_now", "args": {}}]}

    def fixture_models():
        # CI does not install Ollama; tests only browser/API wiring here.
        return ["fixture:model"]

    def fixture_probe(model):
        return {"status": "MODEL_RESPONDED", "model": model,
                "tool_calling": "OBSERVED", "answer_present": True,
                "history_saved": False, "external_dispatch_count": 0}

    def fixture_local_agent(prompt, model, history=None):
        return {"mode": "LOCAL_MODEL", "answer": "Fixture answer: " + str(len(prompt)),
                "tools_used": ["calculate"], "external_dispatch_count": 0}

    def fixture_document(filename,content,question,model):
        return analyze_local_document(
            filename,content,question,model,
            transport=lambda _:{"message":{"content":
                '{"answer":"The desk opens at 09:00.",'
                '"insufficient_evidence":false,'
                '"citations":[{"chunk_id":"S1","quote":"opens at 09:00"}]}'}})

    def fixture_multi_document(documents,question,model):
        return analyze_multiple_documents(
            documents,question,model,
            transport=lambda _:{"message":{"content":
                '{"answer":"The meeting begins at 18:30 WIB.",'
                '"insufficient_evidence":false,'
                '"citations":[{"chunk_id":"F2C1",'
                '"quote":"meeting starts at 18:30 WIB"}]}'}})

    def fixture_device_acceptance(model):
        if model == "fixture:timeout":
            raise AgentError("local_model_timeout")
        return device_smoke(
            model, catalog=lambda:["fixture:model"],
            transport=lambda _:{"message":{"content":
                '{"answer":"Maintenance review meeting starts at 18:30 WIB.",'
                '"insufficient_evidence":false,'
                '"citations":[{"chunk_id":"F2C1",'
                '"quote":"Maintenance review meeting starts at 18:30 WIB"}]}' }},
            memory=lambda:None)

    server = ThreadingHTTPServer(
        ("127.0.0.1", 8765),
        create_handler(local_core=core, operator_auth=owner,
                       source_policy=policy, security_audit=audit,
                       local_agent_runner=fixture_local_agent,
                       agent_conversation=conversation,
                       local_agent_models=fixture_models,
                       local_agent_probe=fixture_probe,
                       local_agent_planner=fixture_planner,
                       local_agent_plan_store=plans,
                       local_document_analyzer=fixture_document,
                       local_multi_document_analyzer=fixture_multi_document,
                       local_device_acceptance=fixture_device_acceptance))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        core.close()
        conversation.close()
        plans.close()
        audit.close()
        home.cleanup()


if __name__ == "__main__":
    main()
