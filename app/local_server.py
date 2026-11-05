"""Loopback-only local prototype and SQLite rule core.

Production local entry requires a provisioned single-operator login; it is
still NOT a public production server, provider writer or AI agent.
The separate local workbench stores pseudonymous event IDs and rule
categories in SQLite; mock workflow state still resets on restart.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import getpass
import json
import socket
import sys
import threading
from urllib.parse import parse_qs, urlsplit
from local_workflow_core import LocalWorkflowCore
from local_operator_auth import LocalOperatorAuth, OperatorConfigurationError
from local_source_policy import LocalPublicSourcePolicy, SourcePolicyError
from local_security_audit import LocalSecurityAudit, SecurityAuditError
from local_csv_batch import LocalCSVBatchIntake
from github_public_issue_intake import PublicGitHubReadOnlyIntake
from local_ollama_agent import run_agent, AgentError, valid_model_name
from local_document_analysis import (analyze_local_document, LocalDocumentError,
                                     validate_document)
from local_multi_document_analysis import (analyze_multiple_documents,
                                           validate_multiple_documents)
from local_agent_task_plans import (LocalAgentPlanStore, PlanError, MAX_PLANS,
                                    propose_readonly_plan)
from local_agent_github_reader import AllowedGitHubIssuesReader
from local_ollama_readiness import installed_models, probe_model
from local_multi_doc_smoke import smoke as run_local_device_acceptance
from local_agent_conversation import (LocalAgentConversation, ConversationError,
                                      valid_conversation_id, DEFAULT_CONVERSATION)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_flow import MockInquiryFlow
from support_triage_flow import MockSupportTriage
from appointment_followup_flow import MockAppointmentFollowup
from crm_handoff_flow import MockCRMHandoff
from knowledge_document_routing_flow import MockKnowledgeRouting
from lead_inquiry_demo_review import LocalMockReview, execution_timeline
from lead_inquiry_template_catalog import (list_templates, setup_choices,
    validate_guided_setup, default_setup)

MAX_BODY = 8192
WEB = ROOT / "web" / "index.html"
_CONFIGURATION = {
    "source": "mock_inbox", "destination": "mock_crm",
    "follow_up_policy": "approval_required", "ai_mode": "mock",
    "timezone": "Asia/Jakarta",
    "mapping": {"inquiry_id": "source_event_id", "service_requested": "service_requested"},
    "monthly_budget_idr": 0,
}
_REQUIRED = ["service_requested", "budget_range", "timeline"]


def create_handler(flow=None, local_core=None, github_fetcher=None, github_clock=None,
                   operator_auth=None, source_policy=None, security_audit=None,
                   local_agent_runner=None, agent_conversation=None,
                   local_agent_models=None, local_agent_probe=None,
                   local_agent_planner=None, local_agent_plan_store=None,
                   local_document_analyzer=None,
                   local_multi_document_analyzer=None,
                   local_device_acceptance=None):
    """Return isolated handler; operator_auth=None ONLY in controlled old tests.

    start_local_server() never runs with None; missing owner config is fatal.
    """
    workflow = flow if flow is not None else MockInquiryFlow()
    lock = threading.Lock()
    review = LocalMockReview()
    triage = MockSupportTriage()
    appointments = MockAppointmentFollowup()
    crm = MockCRMHandoff()
    knowledge = MockKnowledgeRouting()
    core = local_core if local_core is not None else LocalWorkflowCore()
    batch = LocalCSVBatchIntake(core)
    agent_gate = threading.BoundedSemaphore(1)
    public_github = PublicGitHubReadOnlyIntake(core, batch,
                                               fetcher=github_fetcher,
                                               clock=github_clock,
                                               policy=source_policy)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Refuse logging request paths, free text, identities and payloads.
            return

        def _send(self, code, payload, content_type="application/json; charset=utf-8",
                  cookie_header=None):
            body = (payload.encode("utf-8") if isinstance(payload, str)
                    else json.dumps(payload, sort_keys=True).encode("utf-8"))
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            if cookie_header is not None:
                self.send_header("Set-Cookie", cookie_header)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _local_allowed(self):
            host = self.headers.get("Host", "")
            if host not in ("127.0.0.1", "localhost",
                            f"127.0.0.1:{self.server.server_port}",
                            f"localhost:{self.server.server_port}"):
                return "local_host_required"
            origin = self.headers.get("Origin")
            if origin is not None and origin not in (
                    f"http://127.0.0.1:{self.server.server_port}",
                    f"http://localhost:{self.server.server_port}"):
                return "same_origin_required"
            return None

        @staticmethod
        def _no_cookie():
            return "aas_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"

        @staticmethod
        def _login_cookie(token):
            # Secure cannot be set on plain http://127.0.0.1. Never expose
            # this loopback-only HTTP server to LAN/port forwarding.
            return ("aas_session=" + token +
                    "; Max-Age=3600; Path=/; HttpOnly; SameSite=Strict")

        def _operator_token(self):
            # Reject multiple session cookies instead of guessing one.
            from http.cookies import SimpleCookie, CookieError
            raw = self.headers.get("Cookie", "")
            if not raw or len(raw) > 2048 or raw.count("aas_session=") != 1:
                return None
            try:
                parsed = SimpleCookie()
                parsed.load(raw)
                return parsed["aas_session"].value if "aas_session" in parsed else None
            except (CookieError, ValueError):
                return None

        def _operator_authorized(self, mutation=False):
            if operator_auth is None:
                # Deliberately restricted to injected test handlers. Main
                # server always loads a real configured operator verifier.
                return True
            token = self._operator_token()
            inspected = operator_auth.inspect(token)
            if inspected is None:
                self._send(401, {"error": "operator_session_required"})
                return False
            if mutation and not operator_auth.authorize(
                    token, self.headers.get("X-AAS-CSRF")):
                self._send(403, {"error": "operator_csrf_required"})
                return False
            return True

        def _audit_event(self, action, outcome, repo=None):
            if security_audit is None:
                return True
            try:
                security_audit.record(action, outcome, repo)
                return True
            except SecurityAuditError:
                return False

        def do_GET(self):
            route = urlsplit(self.path)
            denied = self._local_allowed()
            if denied:
                self._send(403, {"error": denied})
                return
            if route.path == "/api/operator-session" and not route.query:
                if operator_auth is None:
                    self._send(200, {"authenticated": True,
                                     "test_only_legacy_handler": True})
                else:
                    session = operator_auth.inspect(self._operator_token())
                    if session is None:
                        self._send(200, {"authenticated": False})
                    else:
                        self._send(200, {"authenticated": True, **session})
                return
            if route.path != "/" and route.path != "/api/health":
                if not self._operator_authorized():
                    return
            if route.path == "/api/local-agent-capabilities" and not route.query:
                self._send(200, {"enabled": local_agent_runner is not None,
                                 "tools": ["calculate", "utc_now"],
                                 "local_only": True,
                                 "persisted": agent_conversation is not None,
                                 "documents": (local_agent_runner is not None
                                               and local_document_analyzer is not None),
                                 "multi_documents": (local_agent_runner is not None
                                                     and local_multi_document_analyzer is not None),
                                 "device_acceptance": (local_agent_runner is not None
                                                       and local_device_acceptance is not None),
                                 "planning": (local_agent_planner is not None
                                              and local_agent_plan_store is not None),
                                 "github_plan_read": bool(
                                     local_agent_plan_store is not None
                                     and local_agent_plan_store.github_reader is not None),
                                 "readiness": (local_agent_models is not None
                                               and local_agent_probe is not None)})
                return
            if route.path in ("/api/local-agent-plans", "/api/local-agent-plan"):
                if local_agent_planner is None or local_agent_plan_store is None:
                    self._send(403, {"error": "agent_planning_disabled"})
                    return
                try:
                    if route.path == "/api/local-agent-plans":
                        if route.query:
                            self._send(422, {"error": "invalid_plan_query"})
                        else:
                            self._send(200, {"plans": local_agent_plan_store.list(),
                                             "capacity": MAX_PLANS})
                        return
                    params = parse_qs(route.query, keep_blank_values=True)
                    if (set(params) != {"plan_id"}
                            or len(params["plan_id"]) != 1):
                        self._send(422, {"error": "invalid_plan_query"})
                        return
                    self._send(200, local_agent_plan_store.get(params["plan_id"][0]))
                except PlanError as exc:
                    code = (422 if str(exc).startswith("invalid_") else
                            404 if str(exc) == "plan_not_found" else 503)
                    self._send(code, {"error": str(exc)})
                return
            if route.path == "/api/local-agent-models" and not route.query:
                if local_agent_runner is None or local_agent_models is None:
                    self._send(403, {"error": "local_agent_probe_disabled"})
                    return
                try:
                    models = local_agent_models()
                    self._send(200, {"status": "OLLAMA_REACHABLE", "models": models,
                                     "external_dispatch_count": 0})
                except AgentError as exc:
                    self._send(503, {"error": str(exc)})
                except Exception:
                    self._send(503, {"error": "local_model_unavailable"})
                return
            if route.path in ("/api/local-agent-history",
                              "/api/local-agent-conversations"):
                if local_agent_runner is None or agent_conversation is None:
                    self._send(403, {"error": "agent_history_disabled"})
                    return
                if route.path == "/api/local-agent-conversations":
                    if route.query:
                        self._send(422, {"error": "invalid_conversation_query"})
                        return
                    try:
                        self._send(200, {"conversation_ids":
                                         agent_conversation.list_conversations()})
                    except ConversationError:
                        self._send(503, {"error": "conversation_storage_unavailable"})
                    return
                conversation_id = DEFAULT_CONVERSATION
                if route.query:
                    params = parse_qs(route.query, keep_blank_values=True)
                    if (set(params) != {"conversation_id"}
                            or len(params["conversation_id"]) != 1):
                        self._send(422, {"error": "invalid_conversation_query"})
                        return
                    conversation_id = params["conversation_id"][0]
                if not valid_conversation_id(conversation_id):
                    self._send(422, {"error": "invalid_conversation_id"})
                    return
                try:
                    self._send(200, {"conversation_id": conversation_id,
                                     "turns": agent_conversation.snapshot(conversation_id),
                                     "storage": "local_plaintext"})
                except ConversationError:
                    self._send(503, {"error": "conversation_storage_unavailable"})
                return
            if route.path == "/api/operator-audit":
                params = parse_qs(route.query, keep_blank_values=True)
                if not route.query:
                    count = 40
                elif set(params) == {"limit"} and len(params["limit"]) == 1:
                    try:
                        count = int(params["limit"][0])
                    except ValueError:
                        self._send(422, {"error": "invalid_audit_limit"})
                        return
                else:
                    self._send(422, {"error": "invalid_audit_query"})
                    return
                if security_audit is None:
                    self._send(200, {"events": [], "test_only": True})
                    return
                try:
                    events = security_audit.recent(count)
                except ValueError:
                    self._send(422, {"error": "invalid_audit_limit"})
                    return
                except SecurityAuditError:
                    self._send(503, {"error": "security_audit_restart_required"})
                    return
                self._send(200, {"events": events})
                return
            if route.path == "/api/local-github-sources" and not route.query:
                if source_policy is None:
                    self._send(200, {"allowed": [], "epoch": 0, "test_only": True})
                else:
                    self._send(200, source_policy.snapshot())
                return
            if route.path in ("/api/local-workflows", "/api/local-history", "/api/local-revisions"):
                if route.path == "/api/local-workflows" and not route.query:
                    self._send(200, core.list_workflows())
                    return
                if route.path in ("/api/local-history", "/api/local-revisions"):
                    params = parse_qs(route.query, keep_blank_values=True)
                    if set(params) == {"workflow_id"} and len(params["workflow_id"]) == 1:
                        method = (core.history if route.path == "/api/local-history"
                                  else core.revisions)
                        code, out = method(params["workflow_id"][0])
                        self._send(code, out)
                        return
                self._send(422, {"error": "invalid_local_query"})
                return
            if self.path == "/":
                self._send(200, WEB.read_text(encoding="utf-8"),
                           "text/html; charset=utf-8")
            elif self.path == "/api/health":
                self._send(200, {"status": "MOCK_ONLY", "external_dispatch_count": 0})
            elif self.path == "/api/templates":
                self._send(200, list_templates())
            elif self.path == "/api/setup-choices":
                self._send(200, setup_choices())
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path not in ("/api/operator-login", "/api/operator-logout",
                                 "/api/operator-rotate", "/api/local-github-source-grant",
                                 "/api/local-github-source-revoke",
                                 "/api/mock-run", "/api/mock-review", "/api/mock-setup", "/api/mock-triage", "/api/mock-appointment", "/api/mock-crm-handoff", "/api/mock-knowledge-routing", "/api/local-create", "/api/local-activate", "/api/local-run", "/api/local-batch-preview", "/api/local-batch-commit", "/api/local-stage", "/api/local-publish", "/api/local-pause", "/api/local-resume", "/api/local-github-preview", "/api/local-github-commit",
                                 "/api/local-agent-run", "/api/local-agent-clear",
                                 "/api/local-agent-probe",
                                 "/api/local-agent-document-analyze",
                                 "/api/local-agent-multi-document-analyze",
                                 "/api/local-agent-device-acceptance",
                                 "/api/local-agent-plan-create",
                                 "/api/local-agent-plan-approve",
                                 "/api/local-agent-plan-next",
                                 "/api/local-agent-plan-cancel",
                                 "/api/local-agent-plan-resume",
                                 "/api/local-agent-plan-delete"):
                self._send(404, {"error": "not_found"})
                return
            # Reject cross-origin mutation attempts before reading the body.
            # Operator session+CSRF is checked on every non-login mutation.
            denied = self._local_allowed()
            if denied:
                self._send(403, {"error": denied})
                return
            if self.path != "/api/operator-login":
                if not self._operator_authorized(mutation=True):
                    return
            if (security_audit is not None and not security_audit.ready()
                    and self.path != "/api/operator-logout"):
                self._send(503, {"error": "security_audit_restart_required"})
                return
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                self._send(415, {"error": "json_required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid_length"})
                return
            if not 0 < size <= MAX_BODY:
                self._send(413, {"error": "body_size_rejected"})
                return
            try:
                data = json.loads(self.rfile.read(size))
            except (UnicodeError, ValueError):
                self._send(400, {"error": "invalid_json"})
                return
            if self.path == "/api/local-agent-device-acceptance":
                if local_agent_runner is None or local_device_acceptance is None:
                    self._send(403, {"error": "device_acceptance_disabled"})
                    return
                if (type(data) is not dict or set(data) != {"model"}
                        or not valid_model_name(data.get("model"))):
                    self._send(422, {"error": "invalid_device_acceptance_request"})
                    return
                if not agent_gate.acquire(blocking=False):
                    self._send(429, {"error": "local_agent_busy"})
                    return
                try:
                    try:
                        report = local_device_acceptance(data["model"])
                        if (type(report) is not dict
                                or report.get("status") not in (
                                    "REAL_LOCAL_MULTI_DOCUMENT_SMOKE_PASSED",
                                    "SYNTHETIC_MODEL_SMOKE_PASSED")
                                or report.get("model") != data["model"]
                                or report.get("source_evidence_verified") is not True
                                or report.get("model_calls") != 1
                                or report.get("history_saved") is not False
                                or report.get("external_write_count") != 0):
                            self._send(502, {"error": "invalid_device_acceptance_result"})
                        else:
                            self._send(200, report)
                    except AgentError as exc:
                        error = str(exc)
                        code = (422 if error.startswith("invalid_") else
                                409 if error == "model_not_installed" else 503)
                        self._send(code, {"error": error})
                    except LocalDocumentError as exc:
                        self._send(503, {"error": str(exc)})
                    except Exception:
                        self._send(503, {"error": "local_device_acceptance_unavailable"})
                finally:
                    agent_gate.release()
                return
            if self.path == "/api/local-agent-multi-document-analyze":
                if local_agent_runner is None or local_multi_document_analyzer is None:
                    self._send(403, {"error": "multi_documents_disabled"})
                    return
                if (type(data) is not dict
                        or set(data) != {"documents", "question", "model"}):
                    self._send(422, {"error": "invalid_multi_document_request"})
                    return
                # Preflight is enforced at the authenticated API boundary before
                # any model implementation, including injected test callbacks.
                try:
                    validate_multiple_documents(data["documents"], data["question"],
                                                data["model"])
                except LocalDocumentError as exc:
                    code = 413 if str(exc) in ("multi_document_too_large",
                                                "local_document_too_large") else 422
                    self._send(code, {"error": str(exc)})
                    return
                if not agent_gate.acquire(blocking=False):
                    self._send(429, {"error": "local_agent_busy"})
                    return
                try:
                    try:
                        result = local_multi_document_analyzer(
                            data["documents"], data["question"], data["model"])
                        if (type(result) is not dict
                                or result.get("status") != "MULTI_DOCUMENT_ANALYZED_LOCAL"
                                or result.get("history_saved") is not False
                                or result.get("external_write_count") != 0):
                            self._send(502, {"error": "invalid_multi_document_model_response"})
                        else:
                            self._send(200, result)
                    except LocalDocumentError as exc:
                        error = str(exc)
                        self._send(413 if error in ("multi_document_too_large",
                                                   "local_document_too_large") else
                                   422 if error.startswith("invalid_") else 503,
                                   {"error": error})
                    except Exception:
                        self._send(503, {"error": "local_multi_document_model_unavailable"})
                finally:
                    agent_gate.release()
                return
            if self.path == "/api/local-agent-document-analyze":
                if local_agent_runner is None or local_document_analyzer is None:
                    self._send(403, {"error": "local_documents_disabled"})
                    return
                if (type(data) is not dict
                        or set(data) != {"filename", "content", "question", "model"}
                        or any(type(data[key]) is not str for key in data)):
                    self._send(422, {"error": "invalid_local_document_request"})
                    return
                # Preflight before any model callback (including test adapters).
                try:
                    validate_document(data["filename"], data["content"],
                                      data["question"], data["model"])
                except LocalDocumentError as exc:
                    err = str(exc)
                    self._send(413 if err == "local_document_too_large" else 422,
                               {"error": err})
                    return
                if not agent_gate.acquire(blocking=False):
                    self._send(429, {"error": "local_agent_busy"})
                    return
                try:
                    try:
                        result = local_document_analyzer(
                            data["filename"], data["content"],
                            data["question"], data["model"])
                        if (type(result) is not dict
                                or result.get("status") != "DOCUMENT_ANALYZED_LOCAL"
                                or result.get("history_saved") is not False
                                or result.get("external_write_count") != 0):
                            self._send(502, {"error": "invalid_document_model_response"})
                        else:
                            self._send(200, result)
                    except LocalDocumentError as exc:
                        err = str(exc)
                        code = 413 if err == "local_document_too_large" else (
                            422 if err.startswith("invalid_") else 503)
                        self._send(code, {"error": err})
                    except Exception:
                        self._send(503, {"error": "local_document_model_unavailable"})
                finally:
                    agent_gate.release()
                return
            if self.path.startswith("/api/local-agent-plan-"):
                if local_agent_planner is None or local_agent_plan_store is None:
                    self._send(403, {"error": "agent_planning_disabled"})
                    return
                creating = self.path == "/api/local-agent-plan-create"
                if (type(data) is not dict or
                    (creating and (set(data) != {"goal", "model"}
                        or type(data["goal"]) is not str
                        or type(data["model"]) is not str)) or
                    (not creating and (set(data) != {"plan_id"}
                        or type(data["plan_id"]) is not str))):
                    self._send(422, {"error": "invalid_plan_request"})
                    return
                if not agent_gate.acquire(blocking=False):
                    self._send(429, {"error": "local_agent_busy"})
                    return
                try:
                    try:
                        if creating:
                            if len(local_agent_plan_store.list()) >= MAX_PLANS:
                                raise PlanError("plan_capacity_reached")
                            proposal = local_agent_planner(data["goal"], data["model"])
                            outcome = local_agent_plan_store.create(proposal)
                        else:
                            action = {
                                "/api/local-agent-plan-approve": local_agent_plan_store.approve,
                                "/api/local-agent-plan-next": local_agent_plan_store.advance,
                                "/api/local-agent-plan-cancel": local_agent_plan_store.cancel,
                                "/api/local-agent-plan-resume": local_agent_plan_store.resume_interrupted,
                                "/api/local-agent-plan-delete": local_agent_plan_store.delete,
                            }[self.path]
                            outcome = action(data["plan_id"])
                        self._send(200, outcome)
                    except PlanError as exc:
                        err = str(exc)
                        code = (422 if err.startswith("invalid_") or err == "tool_not_allowed"
                                else 403 if err == "public_source_not_allowed"
                                else 404 if err == "plan_not_found"
                                else 409 if err in ("plan_state_conflict", "plan_capacity_reached",
                                                     "public_source_permission_changed")
                                else 503)
                        self._send(code, {"error": err})
                    except AgentError as exc:
                        self._send(503, {"error": str(exc)})
                    except Exception:
                        self._send(503, {"error": "agent_plan_unavailable"})
                finally:
                    agent_gate.release()
                return
            if self.path == "/api/local-agent-probe":
                if local_agent_runner is None or local_agent_probe is None:
                    self._send(403, {"error": "local_agent_probe_disabled"})
                    return
                if type(data) is not dict or set(data) != {"model"} or type(data["model"]) is not str:
                    self._send(422, {"error": "invalid_probe_request"})
                    return
                if not agent_gate.acquire(blocking=False):
                    self._send(429, {"error": "local_agent_busy"})
                    return
                try:
                    try:
                        result = local_agent_probe(data["model"])
                        if (type(result) is not dict
                                or result.get("status") != "MODEL_RESPONDED"
                                or result.get("external_dispatch_count") != 0):
                            self._send(502, {"error": "invalid_model_probe_result"})
                        else:
                            self._send(200, result)
                    except AgentError as exc:
                        code = (422 if str(exc).startswith("invalid_") else
                                409 if str(exc) == "model_not_installed" else 503)
                        self._send(code, {"error": str(exc)})
                    except Exception:
                        self._send(503, {"error": "local_model_probe_failed"})
                finally:
                    agent_gate.release()
                return
            if self.path == "/api/local-agent-clear":
                if local_agent_runner is None or agent_conversation is None:
                    self._send(403, {"error": "agent_history_disabled"})
                    return
                if (type(data) is not dict
                        or set(data) not in (set(), {"conversation_id"})):
                    self._send(422, {"error": "invalid_clear_fields"})
                    return
                selected_id = data.get("conversation_id")
                if selected_id is not None and not valid_conversation_id(selected_id):
                    self._send(422, {"error": "invalid_conversation_id"})
                    return
                if not agent_gate.acquire(blocking=False):
                    self._send(429, {"error": "local_agent_busy"})
                    return
                try:
                    agent_conversation.clear(selected_id)
                    self._send(200, {"status": "CLEARED_LOCAL",
                                     "conversation_id": selected_id, "turns": []})
                except ConversationError:
                    self._send(503, {"error": "conversation_storage_unavailable"})
                finally:
                    agent_gate.release()
                return
            if self.path == "/api/local-agent-run":
                if local_agent_runner is None:
                    self._send(403, {"error": "local_agent_disabled"})
                    return
                if (type(data) is not dict
                        or not {"model", "prompt"}.issubset(data)
                        or not set(data).issubset(
                            {"model", "prompt", "remember", "conversation_id"})
                        or type(data["model"]) is not str or type(data["prompt"]) is not str
                        or ("remember" in data and type(data["remember"]) is not bool)):

                    self._send(422, {"error": "invalid_local_agent_request"})
                    return
                remember = data.get("remember", False)
                conversation_id = data.get("conversation_id", DEFAULT_CONVERSATION)
                if ("conversation_id" in data and not remember):
                    self._send(422, {"error": "conversation_requires_remember"})
                    return
                if not valid_conversation_id(conversation_id):
                    self._send(422, {"error": "invalid_conversation_id"})
                    return
                if remember and agent_conversation is None:
                    self._send(403, {"error": "agent_history_disabled"})
                    return
                if not agent_gate.acquire(blocking=False):
                    self._send(429, {"error": "local_agent_busy"})
                    return
                try:
                    try:
                        prior = (agent_conversation.snapshot(conversation_id)
                                 if remember else None)
                        if (remember and not prior
                                and conversation_id not in
                                agent_conversation.list_conversations()
                                and len(agent_conversation.list_conversations()) >= 8):
                            self._send(409, {"error": "conversation_capacity_reached"})
                            return
                        if remember and prior and any(
                                pair["model"] != data["model"] for pair in prior):
                            self._send(409, {"error": "agent_history_model_mismatch"})
                            return
                        if remember:
                            outcome = local_agent_runner(data["prompt"], data["model"],
                                                         history=prior)
                        else:
                            outcome = local_agent_runner(data["prompt"], data["model"])
                        if type(outcome) is not dict or outcome.get("mode") != "LOCAL_MODEL":
                            self._send(502, {"error": "invalid_agent_result"})
                        else:
                            if remember:
                                agent_conversation.append(
                                    data["model"], data["prompt"], outcome.get("answer"),
                                    conversation_id)
                            self._send(200, {**outcome, "remembered": remember,
                                             "conversation_id": conversation_id
                                             if remember else None})
                    except ConversationError as exc:
                        code = 409 if str(exc) == "conversation_capacity_reached" else 503
                        self._send(code, {"error": str(exc)})
                    except AgentError as exc:
                        self._send(422 if str(exc).startswith("invalid_") else 503,
                                   {"error": str(exc)})
                    except Exception:
                        self._send(503, {"error": "local_agent_unavailable"})
                finally:
                    agent_gate.release()
                return
            if self.path == "/api/operator-login":
                if operator_auth is None:
                    self._send(403, {"error": "auth_fixture_only"})
                    return
                if type(data) is not dict or set(data) != {"password"}:
                    self._send(422, {"error": "invalid_login_fields"})
                    return
                if security_audit is not None and not security_audit.ready():
                    self._send(503, {"error": "security_audit_restart_required"})
                    return
                status, session = operator_auth.login(data["password"])
                if not self._audit_event("OPERATOR_LOGIN",
                                         "SUCCESS" if status == 200 else "DENIED"):
                    if status == 200:
                        operator_auth.logout(session["cookie_token"])
                    self._send(503, {"error": "security_audit_restart_required"})
                    return
                if status != 200:
                    self._send(status, {"error": (
                        "operator_restart_required" if status == 503
                        else "operator_login_locked" if status == 429
                        else "invalid_operator_credentials")})
                    return
                self._send(200, {"authenticated": True,
                                 "csrf_token": session["csrf_token"],
                                 "expires_in": session["expires_in"]},
                           cookie_header=self._login_cookie(session["cookie_token"]))
                return
            if self.path == "/api/operator-rotate":
                if type(data) is not dict or set(data) != {
                        "current_password", "new_password"}:
                    self._send(422, {"error": "invalid_rotation_fields"})
                    return
                if operator_auth is None:
                    self._send(403, {"error": "auth_fixture_only"})
                    return
                if not self._audit_event("OWNER_ROTATE", "ATTEMPT"):
                    self._send(503, {"error": "security_audit_restart_required"})
                    return
                code, outcome = operator_auth.rotate_password(
                    data["current_password"], data["new_password"])
                final = ("SUCCESS" if code == 200 else
                         "UNCERTAIN" if outcome.get("error") ==
                         "operator_restart_required" else
                         "DENIED" if code in (401, 403, 422, 429) else "FAILED")
                if not self._audit_event("OWNER_ROTATE", final):
                    self._send(503, {"error": "security_audit_restart_required"},
                               cookie_header=self._no_cookie() if code == 200 else None)
                    return
                self._send(code, outcome,
                           cookie_header=self._no_cookie() if code == 200 else None)
                return
            if self.path in ("/api/local-github-source-grant",
                             "/api/local-github-source-revoke"):
                if type(data) is not dict or set(data) != {
                        "repo", "current_password"}:
                    self._send(422, {"error": "invalid_source_permission_fields"})
                    return
                if source_policy is None or operator_auth is None:
                    self._send(403, {"error": "source_policy_unavailable"})
                    return
                action = ("PUBLIC_SOURCE_GRANT" if self.path.endswith("grant")
                          else "PUBLIC_SOURCE_REVOKE")
                if not self._audit_event(action, "ATTEMPT", data["repo"]):
                    self._send(503, {"error": "security_audit_restart_required"})
                    return
                approved = operator_auth.confirm_current_password(
                    data["current_password"])
                if approved != 200:
                    self._audit_event(action, "DENIED", data["repo"])
                    self._send(approved, {"error": (
                        "operator_restart_required" if approved == 503
                        else "operator_login_locked" if approved == 429
                        else "invalid_operator_credentials")})
                    return
                operation = (source_policy.grant if self.path.endswith("grant")
                             else source_policy.revoke)
                code, outcome = operation(data["repo"])
                final = ("SUCCESS" if code == 200 else
                         "UNCERTAIN" if outcome.get("error") ==
                         "source_policy_restart_required" else
                         "DENIED" if code in (401, 403, 404, 409, 422)
                         else "FAILED")
                if not self._audit_event(action, final, data["repo"]):
                    self._send(503, {"error": "security_audit_restart_required"})
                    return
                self._send(code, outcome)
                return
            if self.path == "/api/operator-logout":
                if type(data) is not dict or data:
                    self._send(422, {"error": "invalid_logout_fields"})
                    return
                if operator_auth is not None:
                    operator_auth.logout(self._operator_token())
                # Logout must revoke even if an audit write fails.
                self._audit_event("OPERATOR_LOGOUT", "SUCCESS")
                self._send(200, {"authenticated": False},
                           cookie_header=self._no_cookie())
                return
            if self.path in ("/api/local-github-preview", "/api/local-github-commit"):
                method = (public_github.preview if self.path == "/api/local-github-preview"
                          else public_github.commit)
                code, result = method(data)
                self._send(code, result)
                return
            if self.path in ("/api/local-batch-preview", "/api/local-batch-commit"):
                method = (batch.preview if self.path == "/api/local-batch-preview"
                          else batch.commit)
                code, result = method(data)
                self._send(code, result)
                return
            if self.path in ("/api/local-stage", "/api/local-publish",
                             "/api/local-pause", "/api/local-resume"):
                method = {
                    "/api/local-stage": core.stage_revision,
                    "/api/local-publish": core.publish_revision,
                    "/api/local-pause": lambda req: core.set_paused(req, True),
                    "/api/local-resume": lambda req: core.set_paused(req, False),
                }[self.path]
                code, result = method(data)
                self._send(code, result)
                return
            if self.path in ("/api/local-create", "/api/local-activate", "/api/local-run"):
                # SQLite workbench methods serialize their own writes. Nothing
                # in these endpoints invokes a mock or external provider.
                method = {
                    "/api/local-create": core.create,
                    "/api/local-activate": core.activate,
                    "/api/local-run": core.run,
                }[self.path]
                code, result = method(data)
                self._send(code, result)
                return
            if self.path == "/api/mock-knowledge-routing":
                with lock:
                    result = knowledge.process(data)
                self._send(422 if result["status"] == "BLOCKED" else 200, result)
                return
            if self.path == "/api/mock-crm-handoff":
                with lock:
                    result = crm.process(data)
                self._send(422 if result["status"] == "BLOCKED" else 200, result)
                return
            if self.path == "/api/mock-appointment":
                with lock:
                    result = appointments.process(data)
                self._send(422 if result["status"] == "BLOCKED" else 200, result)
                return
            if self.path == "/api/mock-triage":
                with lock:
                    result = triage.process(data)
                self._send(200 if result["status"] != "BLOCKED" else 422, result)
                return
            if self.path == "/api/mock-setup":
                verdict = validate_guided_setup(data)
                self._send(200 if verdict["status"] == "DRAFT_VALID" else 422, {
                    "mode": "MOCK_ONLY", "setup_status": verdict["status"],
                    "errors": verdict["errors"], "external_dispatch_count": 0})
                return
            if self.path == "/api/mock-review":
                if (type(data) is not dict or set(data) != {"review_token", "decision"}):
                    self._send(422, {"error": "invalid_review_request"})
                    return
                with lock:
                    result = review.decide(data["review_token"], data["decision"])
                self._send(200, {"mode": "MOCK_ONLY", "review": result,
                                 "external_dispatch_count": 0})
                return
            event_fields = {"event_id", "service_requested", "budget_range", "timeline"}
            if (type(data) is not dict
                    or set(data) not in (event_fields, event_fields | {"template_id", "setup"})
                    or any(type(data[k]) is not str or not data[k].strip() or len(data[k]) > 120
                           for k in event_fields)
                    or len(data["event_id"]) > 80):
                self._send(422, {"error": "invalid_synthetic_fields"})
                return
            setup_request = (
                {"template_id": data["template_id"], "setup": data["setup"]}
                if "setup" in data else default_setup())
            setup_verdict = validate_guided_setup(setup_request)
            if setup_verdict["status"] != "DRAFT_VALID":
                self._send(422, {"error": "invalid_mock_setup",
                                 "errors": setup_verdict["errors"]})
                return
            configuration = setup_verdict["configuration"]
            event = {
                "source_event_id": data["event_id"],
                "inquiry": {key: data[key] for key in _REQUIRED},
            }
            # Shared process-local store must not be accessed simultaneously.
            with lock:
                result = workflow.process(configuration, event, _REQUIRED)
                offer = review.offer(result, policy=configuration["follow_up_policy"])
            self._send(200, {"mode": "MOCK_ONLY", "result": result,
                             "timeline": execution_timeline(result, policy=configuration["follow_up_policy"]),
                             "setup_status": setup_verdict["status"],
                             "review_offer": offer,
                             "external_dispatch_count": 0})

    return Handler


def start_local_server(port=8765, enable_local_agent=False,
                       enable_agent_history=False, enable_agent_plans=False,
                       enable_agent_github_read=False,
                       enable_local_documents=False,
                       enable_multi_documents=False):
    """Refuse to bind until one offline owner verifier is provisioned."""
    home = Path.home() / ".ai-automation-studio"
    operator = LocalOperatorAuth.load(home / "operator.json")
    source_policy = LocalPublicSourcePolicy(home / "public-github-sources.json")
    audit = LocalSecurityAudit(home / "security-audit.sqlite3")
    try:
        core = LocalWorkflowCore(home / "local-workflows.sqlite3")
    except BaseException:
        audit.close()
        raise
    if enable_local_documents and not enable_local_agent:
        raise ValueError("local_documents_require_local_agent")
    if enable_multi_documents and not enable_local_agent:
        raise ValueError("multi_documents_require_local_agent")
    if enable_agent_plans and not enable_local_agent:
        raise ValueError("agent_plans_require_local_agent")
    if enable_agent_github_read and not enable_agent_plans:
        raise ValueError("github_plan_read_requires_agent_plans")
    conversation = None
    plan_store = None
    if enable_agent_plans:
        reader = (AllowedGitHubIssuesReader(source_policy)
                  if enable_agent_github_read else None)
        plan_store = LocalAgentPlanStore(
            home / "agent-task-plans.sqlite3", github_reader=reader)
    if enable_agent_history and enable_local_agent:
        conversation = LocalAgentConversation(home / "agent-conversation.sqlite3")
    try:
        server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            create_handler(local_core=core, operator_auth=operator,
                           source_policy=source_policy, security_audit=audit,
                           local_agent_runner=run_agent if enable_local_agent else None,
                           agent_conversation=conversation,
                           local_agent_models=installed_models if enable_local_agent else None,
                           local_agent_probe=probe_model if enable_local_agent else None,
                           local_agent_planner=(
                               (lambda goal, model: propose_readonly_plan(
                                   goal, model, allow_github=enable_agent_github_read))
                               if enable_agent_plans else None),
                           local_agent_plan_store=plan_store,
                           local_document_analyzer=(
                               analyze_local_document if enable_local_documents else None),
                           local_multi_document_analyzer=(
                               analyze_multiple_documents if enable_multi_documents else None),
                           local_device_acceptance=(
                               run_local_device_acceptance if enable_multi_documents else None)))
        print(f"Local AI Automation Studio: http://127.0.0.1:{server.server_port}")
        try:
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        if plan_store is not None:
            plan_store.close()
        if conversation is not None:
            conversation.close()
        core.close()
        audit.close()


def _lock_recovery_to_offline_port():
    """Own the default local server port during one-time recovery.

    This prevents a normal local server running concurrently on 8765, which
    would otherwise retain old sessions/owner credentials in memory.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 8765))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        raise OperatorConfigurationError("stop_local_server_before_recovery") from None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Local-only workflow server with offline owner provisioning.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--init-owner", action="store_true",
                       help="Provision a single operator password offline.")
    group.add_argument("--enable-recovery", action="store_true",
                       help="Create/rotate a high-entropy offline recovery code.")
    group.add_argument("--recover-owner", action="store_true",
                       help="Use a one-time code to reset lost owner password offline.")
    parser.add_argument("--enable-local-agent", action="store_true",
                        help="Explicitly enable authenticated Ollama calls in browser.")
    parser.add_argument("--enable-agent-history", action="store_true",
                        help="Explicit opt-in local plaintext transcript storage.")
    parser.add_argument("--enable-agent-plans", action="store_true",
                        help="Opt in to persistent, human-approved read-only task plans.")
    parser.add_argument("--enable-multi-documents", action="store_true",
                        help="Opt in to 2-3 manually selected text files with bounded retrieval.")
    parser.add_argument("--enable-local-documents", action="store_true",
                        help="Separately opt into owner-selected, in-memory text file analysis.")
    parser.add_argument("--enable-agent-github-read", action="store_true",
                        help="Separately opt in to operator-allowlisted public GitHub issue reads in plans.")
    args = parser.parse_args(argv)
    if args.enable_local_documents and not args.enable_local_agent:
        parser.error("--enable-local-documents requires --enable-local-agent")
    if args.enable_multi_documents and not args.enable_local_agent:
        parser.error("--enable-multi-documents requires --enable-local-agent")
    if args.enable_agent_plans and not args.enable_local_agent:
        parser.error("--enable-agent-plans requires --enable-local-agent")
    if args.enable_agent_github_read and not args.enable_agent_plans:
        parser.error("--enable-agent-github-read requires --enable-agent-plans")
    if args.enable_agent_history and not args.enable_local_agent:
        parser.error("--enable-agent-history also requires --enable-local-agent")
    home = Path.home() / ".ai-automation-studio"
    if args.enable_recovery or args.recover_owner:
        # Hold port ownership until on-disk verifier is fully replaced.
        try:
            recovery_lock = _lock_recovery_to_offline_port()
        except OperatorConfigurationError as exc:
            raise SystemExit("Recovery blocked: " + str(exc)) from None
        with recovery_lock:
            audit = LocalSecurityAudit(home / "security-audit.sqlite3")
            try:
                try:
                    owner = LocalOperatorAuth.load(home / "operator.json")
                except OperatorConfigurationError as exc:
                    raise SystemExit("Recovery blocked: " + str(exc)) from None
                if args.enable_recovery:
                    audit.record("RECOVERY_ENROLL", "ATTEMPT")
                    current = getpass.getpass("Current owner password: ")
                    status, result = owner.setup_recovery(current)
                    audit.record("RECOVERY_ENROLL", (
                        "SUCCESS" if status == 200 else
                        "UNCERTAIN" if result.get("error") == "operator_restart_required"
                        else "DENIED" if status in (401, 403, 422, 429) else "FAILED"))
                    if status != 200:
                        raise SystemExit("Recovery setup blocked: " + result["error"])
                    print("SAVE THIS CODE OFFLINE. IT IS SHOWN ONLY ONCE:")
                    print(result["recovery_code"])
                    print("One-time code enabled. Keep it outside the computer.")
                    return
                audit.record("RECOVERY_RESET", "ATTEMPT")
                code = getpass.getpass("One-time offline recovery code: ")
                fresh = getpass.getpass("New local operator password: ")
                confirm = getpass.getpass("Confirm new password: ")
                if fresh != confirm:
                    raise SystemExit("Recovery blocked: new passwords did not match.")
                status, result = owner.recover_with_code(code, fresh)
                audit.record("RECOVERY_RESET", (
                    "SUCCESS" if status == 200 else
                    "UNCERTAIN" if result.get("error") == "operator_restart_required"
                    else "DENIED" if status in (401, 403, 422, 429) else "FAILED"))
                if status != 200:
                    raise SystemExit("Recovery blocked: " + result["error"])
                print("Owner recovered. Recovery code consumed permanently.")
                print("Start the server and sign in with the new password.")
                print("Re-enable offline recovery with --enable-recovery.")
                return
            finally:
                audit.close()
    if args.init_owner:
        password = getpass.getpass("New local operator password (12+ characters): ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise SystemExit("Owner setup rejected: passwords did not match.")
        try:
            LocalOperatorAuth.provision(
                Path.home() / ".ai-automation-studio" / "operator.json", password)
        except FileExistsError:
            raise SystemExit("Owner exists; refusing to overwrite it.")
        except ValueError:
            raise SystemExit("Owner setup rejected: 12-128 characters required.")
        print("Local operator provisioned; password is never printed.")
        return
    try:
        start_local_server(enable_local_agent=args.enable_local_agent,
                           enable_agent_history=args.enable_agent_history,
                           enable_agent_plans=args.enable_agent_plans,
                           enable_agent_github_read=args.enable_agent_github_read,
                           enable_local_documents=args.enable_local_documents,
                           enable_multi_documents=args.enable_multi_documents)
    except (OperatorConfigurationError, SourcePolicyError, SecurityAuditError) as exc:
        raise SystemExit(
            str(exc) + ": run python app/local_server.py --init-owner first.")


if __name__ == "__main__":
    main()
