"""Browser CI fixture: authenticated loopback server with TEST-ONLY owner.

Production app/local_server.py never accepts an ephemeral/default password:
it requires offline one-time owner provisioning.
"""
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import os
import threading
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_operator_auth import LocalOperatorAuth
from local_source_policy import LocalPublicSourcePolicy
from local_hn_story_policy import HNStoryPolicy
from local_hn_story_reader import AllowedHNStoryReader
from local_hn_job_discovery import HNJobDiscovery
from local_greenhouse_job_evidence import GreenhouseJobEvidence
from local_security_audit import LocalSecurityAudit
from local_workflow_core import LocalWorkflowCore
from local_agent_conversation import LocalAgentConversation
from local_agent_memory import LocalAgentMemory
from local_agent_task_plans import LocalAgentPlanStore
from local_agent_profiles import LocalAgentProfiles
from local_scoped_agent_memory import LocalScopedAgentMemory
from local_on_demand_remote_ai import LocalOnDemandRemoteAI
from local_model_safety import LocalModelSafetyState
from local_durable_plan_queue import LocalDurablePlanQueue
from local_typed_agent_handoff import LocalTypedAgentHandoff
from local_csv_profile import profile_selected_csv
from local_plan_artifacts import LocalPlanArtifactExporter
from local_tool_action_gate import LocalCSVProfileActionGate
from local_agent_github_reader import AllowedGitHubIssuesReader
from local_ollama_agent import AgentError
from local_document_analysis import analyze_local_document
from local_multi_document_analysis import analyze_multiple_documents
from local_multi_doc_smoke import smoke as device_smoke
from local_windows_preflight import preflight as device_preflight
from local_agent_issue_analysis import analyze_public_issue_metadata
from local_agent_plan_reports import generate_completed_plan_report
from local_server import create_handler


def main():
    home = tempfile.TemporaryDirectory()
    owner_path = Path(home.name) / "operator.json"
    LocalOperatorAuth.provision(owner_path, "ci_only_fixture_password_1234")
    owner = LocalOperatorAuth.load(owner_path)
    policy = LocalPublicSourcePolicy()
    hn_policy = HNStoryPolicy()
    def fixture_hn_fetch(ident):
        if ident in range(77712301,77712306):
            return {"id":ident,"type":"job",
                    "title":"SYNTHETIC_HN_JOB_"+str(ident)+" / Remote eligibility unverified",
                    "time":1735689600,"text":"<p>Source unverified; no hiring checks.</p>",
                    "kids":[]}
        if ident == 8863:
            return {"id":8863,"type":"story",
                    "title":"Fixture public HN discussion", "time":1175714200,
                    "kids":[8952,9224],
                    "text":"<p>Fixture owner-granted story only.</p>"}
        if ident in (8952,9224):
            return {"id":ident,"type":"comment","parent":8863,
                    "time":1175714300,
                    "text":"Fixture comment under exact approved story."}
        raise AssertionError("HN resource not owner-granted")
    hn_reader = AllowedHNStoryReader(hn_policy,fetcher=fixture_hn_fetch)
    # Only the test fixture grants a sample public repository on startup.
    # Shipping server starts with no grants.
    policy.grant("Octocat/Hello-World")
    audit = LocalSecurityAudit(Path(home.name) / "security-audit.sqlite3")
    core = LocalWorkflowCore(":memory:")
    conversation = LocalAgentConversation(":memory:")
    memory_cards = LocalAgentMemory(":memory:")
    profiles = LocalAgentProfiles(":memory:")
    scoped_notes = LocalScopedAgentMemory(":memory:")
    remote_asks = LocalOnDemandRemoteAI(":memory:", runner=lambda prompt, model: {
        "mode": "REMOTE_OPENAI_READ_ONLY", "answer": "Synthetic cloud response for "+model,
        "tools_used": [], "external_dispatch_count": 0,
        "usage": {"input_tokens": 15, "output_tokens": 6}
    })
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

    def fixture_final_report(plan):
        # Explicit CI-only model fixture: quote must appear in a real stored
        # tool result. No Ollama backend is contacted.
        quote = plan["results"][0]["output"][:48]
        return generate_completed_plan_report(
            plan, transport=lambda _:{"message":{"content":json.dumps({
                "summary":"The saved read-only steps finished. Interpretations are unverified.",
                "findings":[{"step_index":1,"quote":quote,
                             "interpretation":"This is a saved tool-output excerpt."}]
            })}})
    plans = LocalAgentPlanStore(":memory:",github_reader=github_reader,
                                hn_reader=hn_reader,
                                issue_analyzer=fixture_issue_analyzer,
                                memory_store=memory_cards,
                                agent_profiles=profiles,
                                scoped_memory_store=scoped_notes,
                                report_generator=fixture_final_report,
                                csv_profiler=profile_selected_csv)
    def fixture_planner(goal, model, memory_context=None, document_context=None,
                        document_set_context=None, csv_context=None,
                        hn_allowed_ids=None, agent_context=None):
        # The fixture does not run Ollama. It verifies selected cards reach the
        # proposal callback, without allowing a note to select privileged tools.
        if memory_context:
            assert all(set(c)=={"id","text"} for c in memory_context)
            if agent_context is None:
                assert all(c["id"].startswith("fixture_") for c in memory_context)
        if "hacker news" in goal.lower():
            selected=77712301 if "77712301" in goal else 8863
            assert selected in hn_allowed_ids
            return {"model":model,"goal":goal,
                    "steps":[{"tool":"hn_public_story","args":{"story_id":selected}}]}
        if csv_context is not None:
            assert csv_context["columns"] == ["revenue", "stage"]
            assert csv_context["row_count"] == 3
            assert "SYNTHETIC_PERSON_1" not in json.dumps(csv_context)
            steps = [{"tool": "local_csv_profile",
                      "args": {"columns": ["revenue", "stage"]}}]
            if "handoff" in goal.lower():
                steps.append({"tool": "calculate",
                              "args": {"a": {"from_step": 1,
                                             "field": "column_sum",
                                             "column": "revenue"},
                                       "b": "2", "operation": "multiply"}})
            return {"model": model, "goal": goal, "steps": steps}
        if document_set_context is not None:
            assert [d["filename"] for d in document_set_context] == [
                "fixture-runbook.txt", "fixture-meeting.md"]
            assert all(d["source_bytes"] > 0 for d in document_set_context)
            return {"model": model, "goal": goal,
                    "steps": [{"tool":"local_multi_document_question",
                               "args":{"question":"When does the review meeting start?"}}]}
        if document_context is not None:
            assert document_context["filename"] == "fixture-plan-notes.txt"
            assert document_context["source_bytes"] > 0
            return {"model": model, "goal": goal,
                    "steps": [{"tool":"local_document_question",
                               "args":{"question":"When does the service desk open?"}}]}
        if "handoff" in goal.lower():
            return {"model": model, "goal": goal,
                    "steps": [
                        {"tool":"calculate", "args":{
                            "a":"7","b":"8","operation":"multiply"}},
                        {"tool":"calculate", "args":{
                            "a":{"from_step":1,"field":"output"},
                            "b":"2","operation":"multiply"}}]}
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

    def fixture_local_agent(prompt, model, history=None, memory_context=None):
        ids = [note["id"] for note in memory_context] if memory_context else []
        return {"mode": "LOCAL_MODEL",
                "answer": "Fixture answer: " + str(len(prompt))+
                          (" | selected note IDs: "+",".join(ids) if ids else ""),
                "tools_used": ["calculate"], "external_dispatch_count": 0}

    def fixture_document(filename,content,question,model):
        return analyze_local_document(
            filename,content,question,model,
            transport=lambda _:{"message":{"content":
                '{"answer":"The desk opens at 09:00.",'
                '"insufficient_evidence":false,'
                '"citations":[{"chunk_id":"S1","quote":"opens at 09:00"}]}'}})

    # The fixture has opt-in document analysis enabled; it never reads a path.
    plans.document_analyzer = fixture_document

    def fixture_multi_document(documents,question,model):
        return analyze_multiple_documents(
            documents,question,model,
            transport=lambda _:{"message":{"content":
                '{"answer":"The meeting begins at 18:30 WIB.",'
                '"insufficient_evidence":false,'
                '"citations":[{"chunk_id":"F2C1",'
                '"quote":"meeting starts at 18:30 WIB"}]}'}})

    plans.multi_document_analyzer = fixture_multi_document

    if os.environ.get("AAS_FIXTURE_DELEGATIONS") == "1":
        # Opt-in synthetic test plans; no provider/model tool execution yet.
        for ident in ("research_browser", "review_browser"):
            profiles.create({"agent_id":ident,"name":ident.replace("_"," ").title(),
                "category":"research","description":"Synthetic governed Agent",
                "instructions":"Use only approved plans.",
                "allowed_tools":["calculate","utc_now"]})
            scoped_notes.put({"project_id":"browser_project","agent_id":ident,
                "note_id":"note_"+ident,"text":"Owner synthetic scoped note",
                "source_kind":"OWNER_NOTE","source_label":"CI-only fixture",
                "retention_days":30,"expected_revision":0})
        for pid,ident,tool in [("a"*24,"research_browser","calculate"),("b"*24,"review_browser","utc_now")]:
            refs=scoped_notes.select("browser_project",ident,["note_"+ident])[1]
            plan={"model":"fixture:model","goal":"Synthetic approved "+ident,
                "steps":[{"tool":tool,"args":({"a":"7","b":"8","operation":"multiply"} if tool=="calculate" else {})}]}
            plans.create(plan,plan_id=pid,
                agent_binding=profiles.context(ident)["binding"],scoped_memory_refs=refs)
            plans.approve(pid)

    def fixture_device_acceptance(model):
        if model == "fixture:timeout":
            raise AgentError("local_model_timeout")
        if model == "fixture:disconnect":
            raise AgentError("local_model_transport_uncertain")
        return device_smoke(
            model, catalog=lambda:["fixture:model"],
            transport=lambda _:{"message":{"content":
                '{"answer":"Maintenance review meeting starts at 18:30 WIB.",'
                '"insufficient_evidence":false,'
                '"citations":[{"chunk_id":"F2C1",'
                '"quote":"Maintenance review meeting starts at 18:30 WIB"}]}' }},
            memory=lambda:None)

    safety=LocalModelSafetyState(Path(home.name) / "safety.sqlite3")
    gate=threading.BoundedSemaphore(1)
    gate_lock=threading.Lock()
    queue=LocalDurablePlanQueue(plans,safety,gate,gate_lock,Path(home.name) / "queue.sqlite3")
    typed=LocalTypedAgentHandoff(queue,Path(home.name) / "typed-handoffs.sqlite3")

    server = ThreadingHTTPServer(
        ("127.0.0.1", 8765),
        create_handler(local_core=core, operator_auth=owner,
                       source_policy=policy, security_audit=audit,
                       hn_policy=hn_policy,
                       hn_job_discovery=HNJobDiscovery(
                           list_fetcher=lambda:list(range(77712301,77712307)),
                           item_fetcher=fixture_hn_fetch),
                       greenhouse_evidence=GreenhouseJobEvidence(
                           fetcher=lambda board,job: {
                               "id":1234567,
                               "title":"SYNTHETIC ATS Integration Engineer",
                               "company_name":"Fixture Corp",
                               "location":{"name":"US-only / simulated"},
                               "first_published":"2026-10-01T10:00:00Z",
                               "updated_at":"2026-10-03T10:00:00Z",
                               "content":"<p>Candidates must be based in the United States. 3+ years of experience required. Remote US-only.</p>",
                               "absolute_url":"https://evil.invalid/untrusted",
                           } if board=="fixturecorp" and job==1234567
                             else (_ for _ in ()).throw(
                                 RuntimeError("unapproved ATS target"))),
                       local_agent_runner=fixture_local_agent,
                       agent_conversation=conversation,
                       agent_memory_store=memory_cards,
                       agent_profiles=profiles,
                       scoped_memory_store=scoped_notes,
                       on_demand_ai=remote_asks,
                       model_safety_state=safety,
                       shared_agent_gate=gate,
                       shared_agent_state_lock=gate_lock,
                       durable_jobs=queue,
                       typed_handoff=typed,
                       local_agent_models=fixture_models,
                       local_agent_probe=fixture_probe,
                       local_agent_planner=fixture_planner,
                       local_agent_plan_store=plans,
                       plan_artifact_exporter=LocalPlanArtifactExporter(
                           Path(home.name) / "exports"),
                       plan_action_gate=LocalCSVProfileActionGate(
                           plans, Path(home.name) / "exports", audit),
                       local_document_analyzer=fixture_document,
                       local_multi_document_analyzer=fixture_multi_document,
                       local_device_acceptance=fixture_device_acceptance,
                       local_device_preflight=(lambda: device_preflight(
                           catalog=lambda:["fixture:model"],
                           memory=lambda:{"total_ram_mb":8192,
                                          "available_ram_mb":1664,
                                          "memory_load_pct":79},
                           system="Windows"))))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        core.close()
        conversation.close()
        memory_cards.close()
        plans.close()
        profiles.close()
        scoped_notes.close()
        remote_asks.close()
        typed.close()
        queue.close()
        safety.close()
        audit.close()
        home.cleanup()


if __name__ == "__main__":
    main()
