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
import os
import sqlite3
import socket
import sys
import threading
from urllib.parse import parse_qs, urlsplit
from local_workflow_core import LocalWorkflowCore
from local_outbound_webhook import LocalOutboundWebhook, OutboundError
from local_scheduled_webhook import ScheduledWebhookAutopilot, AutopilotError
from local_recurring_scheduler import LocalRecurringScheduler, ScheduleError
from local_operator_auth import LocalOperatorAuth, OperatorConfigurationError
from local_source_policy import LocalPublicSourcePolicy, SourcePolicyError
from local_hn_story_policy import HNStoryPolicy, valid_story_id
from local_hn_story_reader import AllowedHNStoryReader
from local_hn_job_discovery import HNJobDiscovery, HNDiscoveryError
from local_greenhouse_job_evidence import (
    GreenhouseJobEvidence, GreenhouseEvidenceError,
    validate_greenhouse_target)
from local_security_audit import LocalSecurityAudit, SecurityAuditError
from local_csv_batch import LocalCSVBatchIntake
from local_csv_profile import profile_selected_csv, csv_source_metadata
from github_public_issue_intake import PublicGitHubReadOnlyIntake
from local_ollama_agent import run_agent, AgentError, valid_model_name
from local_document_analysis import (analyze_local_document, LocalDocumentError,
                                     validate_document)
from local_multi_document_analysis import (analyze_multiple_documents,
                                           validate_multiple_documents)
from local_agent_batch_runner import LocalAgentBatchRunner
from local_plan_artifacts import LocalPlanArtifactExporter, PlanArtifactError
from local_tool_registry import (
    ACTION_TOOL, MARKDOWN_ACTION_TOOL, HN_DISCOVERY_PREVIEW,
    GREENHOUSE_EVIDENCE, require_owner_action, require_discovery_preview,
    ToolPermissionError, registered_tools)
from local_tool_action_gate import LocalCSVProfileActionGate, ToolActionError
from local_agent_task_plans import (LocalAgentPlanStore, PlanError, MAX_PLANS,
                                    propose_readonly_plan, validated_document_snapshot,
                                    document_source_metadata, validated_document_set_snapshot,
                                    document_set_source_metadata,
                                    validated_csv_snapshot)
from local_agent_github_reader import AllowedGitHubIssuesReader
from local_ollama_readiness import installed_models, probe_model
from local_multi_doc_smoke import smoke as run_local_device_acceptance
from local_windows_preflight import preflight as check_local_device_preflight
from local_model_safety import LocalModelSafetyState, ModelSafetyError
from local_agent_memory import (LocalAgentMemory, MemoryCardError, MAX_MEMORY_CARDS,
                                MAX_SELECTED_MEMORY, valid_selection)
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
MISSION = ROOT / "web" / "mission.html"
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
                   local_device_acceptance=None,
                   local_device_preflight=None, model_safety_state=None,
                   agent_memory_store=None, plan_artifact_exporter=None,
                   plan_action_gate=None, hn_policy=None,
                   hn_job_discovery=None, greenhouse_evidence=None,
                   local_scheduler=None, outbound_webhook=None,
                   scheduled_autopilot=None):
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
    hn_discovery_gate = threading.Lock()
    greenhouse_check_gate = threading.Lock()
    # Production injects a durable local SQLite checkpoint. Tests default to
    # isolated in-memory state and may explicitly supply a disk-backed store.
    model_safety = model_safety_state if model_safety_state is not None else LocalModelSafetyState()
    agent_state_lock = threading.Lock()
    batch_runner = (LocalAgentBatchRunner(local_agent_plan_store, model_safety,
                                        agent_gate, agent_state_lock)
                    if local_agent_plan_store is not None else None)
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
            # Fully commit completion and release inference gate BEFORE the
            # client observes the response. A next sequential call must not
            # race a slow SQLite fsync from the previous finished request.
            if getattr(self, "_agent_gate_acquired", False):
                if not self._leave_agent_gate():
                    code, payload = 503, {"error": "model_safety_storage_unavailable"}
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

        def _enter_agent_gate(self, *, allow_uncertain=False):
            self._model_checkpoint_started = False
            self._agent_gate_acquired = False
            with agent_state_lock:
                if not agent_gate.acquire(blocking=False):
                    self._send(429, {"error": "local_agent_busy"})
                    return False
                self._agent_gate_acquired = True
                if allow_uncertain:
                    return True
                try:
                    # Durable commit BEFORE any model callback, including
                    # injected callbacks. In-flight after crash blocks restart.
                    model_safety.begin()
                    self._model_checkpoint_started = True
                    return True
                except ModelSafetyError as exc:
                    self._agent_gate_acquired = False
                    agent_gate.release()
                    self._send(423 if str(exc) == "model_safety_quarantined"
                               else 503,
                               {"error": "local_ollama_timeout_unresolved"
                                if str(exc) == "model_safety_quarantined"
                                else "model_safety_storage_unavailable"})
                    return False

        def _leave_agent_gate(self):
            if not getattr(self, "_agent_gate_acquired", False):
                return True
            self._agent_gate_acquired = False
            checkpoint_ok = True
            try:
                if getattr(self, "_model_checkpoint_started", False):
                    # If marked UNCERTAIN, finish never clears it.
                    model_safety.finish()
            except ModelSafetyError:
                # Do not send HTTP 200 if final checkpoint commit failed.
                # Further model dispatch also fails closed.
                checkpoint_ok = False
            finally:
                agent_gate.release()
            return checkpoint_ok

        @staticmethod
        def _mark_model_uncertainty(exc):
            if str(exc) in ("local_model_timeout", "local_model_transport_uncertain"):
                try:
                    # Ambiguous socket failure may occur after HTTP POST dispatch;
                    # never mark that model request as proven complete.
                    model_safety.timeout()
                except ModelSafetyError:
                    # Database failures latch a fail-closed state.
                    pass

        def _timeout_status(self):
            phase = model_safety.phase()
            blocked = phase == "UNCERTAIN"
            return {"status": ("OPERATOR_INSPECTION_REQUIRED" if blocked
                               else "MODEL_OPERATION_IN_FLIGHT" if phase == "IN_FLIGHT"
                               else "NO_UNRESOLVED_TIMEOUT"),
                    "timeout_unresolved": blocked,
                    "checkpoint_state": phase,
                    "backend_stopped_verified": False,
                    "next_action": ("Inspect Ollama's actual running work before "
                                    "acknowledging. The application cannot prove "
                                    "the backend request has stopped." if blocked else
                                    "Another model operation is in progress." if
                                    phase == "IN_FLIGHT" else
                                    "No unresolved timeout/crash checkpoint recorded.")}

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
            if route.path not in ("/", "/mission", "/api/health"):
                if not self._operator_authorized():
                    return
            if route.path == "/api/local-agent-capabilities" and not route.query:
                self._send(200, {"enabled": local_agent_runner is not None,
                                 "tools": ["calculate", "utc_now"],
                                 "local_only": True,
                                 "persisted": agent_conversation is not None,
                                 "memory_cards": (local_agent_runner is not None
                                                  and agent_memory_store is not None),
                                 "documents": (local_agent_runner is not None
                                               and local_document_analyzer is not None),
                                 "multi_documents": (local_agent_runner is not None
                                                     and local_multi_document_analyzer is not None),
                                 "device_acceptance": (local_agent_runner is not None
                                                       and local_device_acceptance is not None),
                                 "device_preflight": (local_agent_runner is not None
                                                     and local_device_preflight is not None),
                                 "planning": (local_agent_planner is not None
                                              and local_agent_plan_store is not None),
                                 "plan_server_batches": (batch_runner is not None
                                                         and local_agent_planner is not None),
                                 "plan_handoffs": (local_agent_planner is not None
                                                   and local_agent_plan_store is not None),
                                 "plan_action_export": (
                                     local_agent_planner is not None
                                     and local_agent_plan_store is not None
                                     and local_agent_plan_store.csv_profiler is not None
                                     and plan_artifact_exporter is not None
                                     and plan_action_gate is not None),
                                 "tool_registry": registered_tools(
                                     planning=(local_agent_planner is not None
                                               and local_agent_plan_store is not None),
                                     github=(local_agent_plan_store is not None
                                             and local_agent_plan_store.github_reader is not None),
                                     hn=(local_agent_plan_store is not None
                                         and local_agent_plan_store.hn_reader is not None),
                                     hn_discovery=(hn_job_discovery is not None),
                                     greenhouse=(greenhouse_evidence is not None),
                                     document=(local_agent_plan_store is not None
                                               and local_agent_plan_store.document_analyzer is not None),
                                     documents=(local_agent_plan_store is not None
                                                and local_agent_plan_store.multi_document_analyzer is not None),
                                     csv=(local_agent_plan_store is not None
                                          and local_agent_plan_store.csv_profiler is not None),
                                     artifact=(plan_artifact_exporter is not None),
                                     action=(plan_action_gate is not None)),
                                 "plan_artifact_export": (local_agent_planner is not None
                                                          and local_agent_plan_store is not None
                                                          and plan_artifact_exporter is not None),
                                 "plan_csv": (local_agent_planner is not None
                                              and local_agent_plan_store is not None
                                              and local_agent_plan_store.csv_profiler is not None),
                                 "plan_reports": (local_agent_planner is not None
                                                  and local_agent_plan_store is not None),
                                 "plan_documents": (local_agent_planner is not None
                                                    and local_agent_plan_store is not None
                                                    and local_document_analyzer is not None
                                                    and local_agent_plan_store.document_analyzer is not None),
                                 "plan_multi_documents": (
                                     local_agent_planner is not None
                                     and local_agent_plan_store is not None
                                     and local_multi_document_analyzer is not None
                                     and local_agent_plan_store.multi_document_analyzer is not None),
                                 "plan_memory": (local_agent_planner is not None
                                                 and local_agent_plan_store is not None
                                                 and agent_memory_store is not None),
                                 "greenhouse_evidence": bool(
                                     local_agent_planner is not None
                                     and local_agent_plan_store is not None
                                     and greenhouse_evidence is not None),
                                 "hn_job_discovery": bool(
                                     local_agent_planner is not None
                                     and local_agent_plan_store is not None
                                     and local_agent_plan_store.hn_reader is not None
                                     and hn_policy is not None
                                     and hn_job_discovery is not None),
                                 "hn_story_read": bool(
                                     local_agent_planner is not None
                                     and local_agent_plan_store is not None
                                     and local_agent_plan_store.hn_reader is not None
                                     and hn_policy is not None),
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
            if route.path == "/api/local-agent-timeout-status":
                if route.query:
                    self._send(422, {"error": "invalid_timeout_status_query"})
                elif local_agent_runner is None:
                    self._send(403, {"error": "local_agent_disabled"})
                else:
                    try:
                        self._send(200, self._timeout_status())
                    except ModelSafetyError:
                        self._send(503, {"error": "model_safety_storage_unavailable"})
                return
            if route.path == "/api/local-agent-device-preflight":
                if route.query:
                    self._send(422, {"error": "invalid_preflight_query"})
                    return
                if local_agent_runner is None or local_device_preflight is None:
                    self._send(403, {"error": "device_preflight_disabled"})
                    return
                try:
                    report = local_device_preflight()
                    if (type(report) is not dict
                            or report.get("source") != "LOCAL_OLLAMA_HOST_PREFLIGHT"
                            or report.get("live_model_inference_attempted") is not False
                            or report.get("external_write_count") != 0
                            or report.get("history_saved") is not False):
                        self._send(502, {"error": "invalid_device_preflight_result"})
                    else:
                        self._send(200, report)
                except Exception:
                    self._send(503, {"error": "local_device_preflight_unavailable"})
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
            if route.path == "/api/local-agent-memory":
                if local_agent_runner is None or agent_memory_store is None:
                    self._send(403, {"error": "agent_memory_disabled"})
                elif route.query:
                    self._send(422, {"error": "invalid_memory_query"})
                else:
                    try:
                        self._send(200, {
                            "cards": agent_memory_store.list_cards(),
                            "capacity": MAX_MEMORY_CARDS,
                            "max_selected": MAX_SELECTED_MEMORY,
                            "storage": "PLAINTEXT_LOCAL_SQLITE",
                            "source": "OWNER_CURATED_UNVERIFIED"})
                    except MemoryCardError:
                        self._send(503, {"error": "memory_storage_unavailable"})
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
            if route.path == "/api/local-hn-stories" and not route.query:
                if hn_policy is None:
                    self._send(403, {"error":"hn_story_read_disabled"})
                else:
                    self._send(200, hn_policy.snapshot())
                return
            if route.path == "/api/local-github-sources" and not route.query:
                if source_policy is None:
                    self._send(200, {"allowed": [], "epoch": 0, "test_only": True})
                else:
                    self._send(200, source_policy.snapshot())
                return
            if route.path == "/api/autopilot-status" and not route.query:
                if scheduled_autopilot is None:
                    self._send(200, {"enabled": False, "policies": [], "attempts": [],
                                     "reason": "outbound_webhook_not_enabled"})
                else:
                    try:
                        self._send(200, {"enabled": True, **scheduled_autopilot.list()})
                    except (sqlite3.Error, OSError):
                        self._send(503, {"error": "autopilot_storage_unavailable"})
                return
            if route.path in ("/api/connector-catalog", "/api/outbound-history"):
                if outbound_webhook is None:
                    self._send(200 if route.path == "/api/connector-catalog" else 403,
                               {"connectors": [], "webhook_enabled": False}
                               if route.path == "/api/connector-catalog" else
                               {"error": "outbound_webhook_disabled"})
                else:
                    self._send(200, outbound_webhook.catalog()
                               if route.path == "/api/connector-catalog" else
                               outbound_webhook.history())
                return
            if route.path in ("/api/local-schedules", "/api/local-schedule-history"):
                if local_scheduler is None:
                    self._send(403, {"error": "recurring_scheduler_disabled"})
                    return
                if route.path == "/api/local-schedules" and not route.query:
                    try:
                        self._send(200, local_scheduler.list())
                    except (sqlite3.Error, OSError):
                        self._send(503, {"error": "scheduler_storage_unavailable"})
                    return
                if route.path == "/api/local-schedule-history":
                    params = parse_qs(route.query, keep_blank_values=True)
                    if set(params) == {"schedule_id"} and len(params["schedule_id"]) == 1:
                        try:
                            self._send(200, local_scheduler.history(params["schedule_id"][0]))
                        except ScheduleError as exc:
                            self._send(exc.status, {"error": str(exc)})
                        except (sqlite3.Error, OSError):
                            self._send(503, {"error": "scheduler_storage_unavailable"})
                        return
                self._send(422, {"error": "invalid_schedule_query"})
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
            elif self.path == "/mission":
                self._send(200, MISSION.read_text(encoding="utf-8"),
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
                                 "/api/operator-rotate",
                                 "/api/local-hn-jobstories-preview",
                                 "/api/local-greenhouse-job-preview",
                                 "/api/local-hn-story-grant",
                                 "/api/local-hn-story-revoke",
                                 "/api/local-github-source-grant",
                                 "/api/local-github-source-revoke",
                                 "/api/mock-run", "/api/mock-review", "/api/mock-setup", "/api/mock-triage", "/api/mock-appointment", "/api/mock-crm-handoff", "/api/mock-knowledge-routing", "/api/local-create", "/api/local-activate", "/api/local-run", "/api/local-batch-preview", "/api/local-batch-commit", "/api/local-stage", "/api/local-publish", "/api/local-pause", "/api/local-resume", "/api/local-github-preview", "/api/local-github-commit",
                                 "/api/local-agent-run", "/api/local-agent-clear",
                                 "/api/local-agent-memory-save",
                                 "/api/local-agent-memory-delete",
                                 "/api/local-agent-probe",
                                 "/api/local-agent-document-analyze",
                                 "/api/local-agent-multi-document-analyze",
                                 "/api/local-agent-device-acceptance",
                                 "/api/local-agent-timeout-acknowledge",
                                 "/api/local-agent-plan-create",
                                 "/api/local-agent-plan-approve",
                                 "/api/local-agent-plan-next",
                                 "/api/local-agent-plan-batch-start",
                                 "/api/local-agent-plan-batch-stop",
                                 "/api/local-agent-plan-cancel",
                                 "/api/local-agent-plan-resume",
                                 "/api/local-agent-plan-delete",
                                 "/api/local-agent-plan-report",
                                 "/api/local-agent-plan-export",
                                 "/api/local-agent-action-preview",
                                 "/api/local-agent-action-commit",
                                 "/api/local-schedule-create",
                                 "/api/local-schedule-pause",
                                 "/api/local-schedule-resume",
                                 "/api/outbound-webhook-preview",
                                 "/api/outbound-webhook-commit",
                                 "/api/autopilot-preview",
                                 "/api/autopilot-arm",
                                 "/api/autopilot-stop"):
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
            if self.path in ("/api/local-agent-memory-save",
                             "/api/local-agent-memory-delete"):
                if local_agent_runner is None or agent_memory_store is None:
                    self._send(403, {"error": "agent_memory_disabled"})
                    return
                saving = self.path == "/api/local-agent-memory-save"
                expected = ({"id", "text", "expected_revision"} if saving
                            else {"id", "expected_revision"})
                if type(data) is not dict or set(data) != expected:
                    self._send(422, {"error": "invalid_memory_request"})
                    return
                try:
                    card = (agent_memory_store.put(
                        data["id"], data["text"], data["expected_revision"])
                        if saving else agent_memory_store.delete(
                            data["id"], data["expected_revision"]))
                    self._send(200, card)
                except MemoryCardError as exc:
                    error = str(exc)
                    code = (422 if error.startswith("invalid_") else
                            409 if error in ("memory_capacity_reached",
                                             "memory_revision_conflict") else 503)
                    self._send(code, {"error": error})
                return
            if self.path == "/api/local-agent-timeout-acknowledge":
                if local_agent_runner is None:
                    self._send(403, {"error": "local_agent_disabled"})
                    return
                if (type(data) is not dict
                        or data != {"acknowledge": "OLLAMA_INSPECTED"}):
                    self._send(422, {"error": "invalid_timeout_acknowledgement"})
                    return
                with agent_state_lock:
                    # No re-arming while a request is actually in flight.
                    if not agent_gate.acquire(blocking=False):
                        self._send(429, {"error": "local_agent_busy"})
                        return
                    try:
                        try:
                            model_safety.acknowledge()
                        except ModelSafetyError as exc:
                            self._send(423 if str(exc) == "model_safety_active"
                                       else 503,
                                       {"error": "local_ollama_timeout_unresolved"
                                        if str(exc) == "model_safety_active"
                                        else "model_safety_storage_unavailable"})
                            return
                    finally:
                        agent_gate.release()
                self._send(200, {"status": "OPERATOR_ACKNOWLEDGED",
                                 "timeout_unresolved": False,
                                 "backend_stopped_verified": False,
                                 "model_calls": 0,
                                 "external_write_count": 0})
                return
            if self.path == "/api/local-agent-device-acceptance":
                if local_agent_runner is None or local_device_acceptance is None:
                    self._send(403, {"error": "device_acceptance_disabled"})
                    return
                if (type(data) is not dict or set(data) != {"model"}
                        or not valid_model_name(data.get("model"))):
                    self._send(422, {"error": "invalid_device_acceptance_request"})
                    return
                if not self._enter_agent_gate():
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
                        self._mark_model_uncertainty(exc)
                        error = str(exc)
                        code = (422 if error.startswith("invalid_") else
                                409 if error == "model_not_installed" else 503)
                        self._send(code, {"error": error})
                    except LocalDocumentError as exc:
                        self._mark_model_uncertainty(exc)
                        error = str(exc)
                        self._send(502 if error == "multi_document_numeric_evidence_missing"
                                   else 503, {"error": error})
                    except Exception:
                        self._send(503, {"error": "local_device_acceptance_unavailable"})
                finally:
                    self._leave_agent_gate()
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
                    self._mark_model_uncertainty(exc)
                    code = 413 if str(exc) in ("multi_document_too_large",
                                                "local_document_too_large") else 422
                    self._send(code, {"error": str(exc)})
                    return
                if not self._enter_agent_gate():
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
                        self._mark_model_uncertainty(exc)
                        error = str(exc)
                        self._send(413 if error in ("multi_document_too_large",
                                                   "local_document_too_large") else
                                   502 if error == "multi_document_numeric_evidence_missing" else
                                   422 if error.startswith("invalid_") else 503,
                                   {"error": error})
                    except Exception:
                        self._send(503, {"error": "local_multi_document_model_unavailable"})
                finally:
                    self._leave_agent_gate()
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
                    self._mark_model_uncertainty(exc)
                    err = str(exc)
                    self._send(413 if err == "local_document_too_large" else 422,
                               {"error": err})
                    return
                if not self._enter_agent_gate():
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
                        self._mark_model_uncertainty(exc)
                        err = str(exc)
                        code = 413 if err == "local_document_too_large" else (
                            502 if err == "document_numeric_evidence_missing" else
                            422 if err.startswith("invalid_") else 503)
                        self._send(code, {"error": err})
                    except Exception:
                        self._send(503, {"error": "local_document_model_unavailable"})
                finally:
                    self._leave_agent_gate()
                return
            if self.path in ("/api/local-agent-action-preview",
                             "/api/local-agent-action-commit"):
                if (plan_action_gate is None or plan_artifact_exporter is None
                        or local_agent_plan_store is None
                        or local_agent_plan_store.csv_profiler is None):
                    self._send(403, {"error": "local_tool_permission_denied"})
                    return
                previewing = self.path.endswith("preview")
                expected = ({"action", "plan_id"} if previewing
                            else {"action", "plan_id", "preview_token",
                                  "preview_sha256"})
                if (type(data) is not dict or set(data) != expected
                        or data.get("action") != ACTION_TOOL
                        or any(type(data.get(k)) is not str for k in expected)):
                    self._send(422, {"error": "invalid_action_request"})
                    return
                try:
                    result = (plan_action_gate.preview(
                        data["action"], data["plan_id"]) if previewing else
                        plan_action_gate.commit(
                            data["action"], data["plan_id"],
                            data["preview_token"], data["preview_sha256"]))
                    self._send(200, result)
                except PlanError as exc:
                    err = str(exc)
                    self._send(404 if err == "plan_not_found" else
                               422 if err.startswith("invalid_") else 409,
                               {"error": err})
                except ToolActionError as exc:
                    err = str(exc)
                    self._send(422 if err.startswith("invalid_") else
                               403 if err in ("local_tool_permission_denied",
                                              "local_csv_permission_revoked") else
                               409 if err in (
                                   "action_requires_completed_plan",
                                   "action_batch_active",
                                   "action_csv_source_unavailable",
                                   "action_requires_csv_profile",
                                   "action_profile_invalid",
                                   "action_preview_expired_or_changed",
                                   "action_target_conflict") else 503,
                               {"error": err})
                except Exception:
                    self._send(503, {"error": "local_action_unavailable"})
                return
            if self.path.startswith("/api/local-agent-plan-"):
                if local_agent_planner is None or local_agent_plan_store is None:
                    self._send(403, {"error": "agent_planning_disabled"})
                    return
                creating = self.path == "/api/local-agent-plan-create"
                if (type(data) is not dict or
                    (creating and (
                        not {"goal", "model"}.issubset(set(data))
                        or not set(data).issubset(
                            {"goal", "model", "memory_ids", "document", "documents", "csv"})
                        or type(data["goal"]) is not str
                        or type(data["model"]) is not str)) or
                    (not creating and (set(data) != {"plan_id"}
                        or type(data["plan_id"]) is not str))):
                    self._send(422, {"error": "invalid_plan_request"})
                    return
                selected_memory_ids = data.get("memory_ids") if creating else None
                if creating and "memory_ids" in data:
                    if not valid_selection(selected_memory_ids):
                        self._send(422, {"error": "invalid_memory_selection"})
                        return
                    if agent_memory_store is None:
                        self._send(403, {"error": "agent_memory_disabled"})
                        return
                source_snapshot = None
                source_metadata = None
                source_set_snapshot = None
                source_set_metadata = None
                csv_snapshot = None
                csv_metadata = None
                if creating and "documents" in data and "document" in data:
                    self._send(422, {"error": "invalid_plan_document_selection"})
                    return
                if creating and "csv" in data and (
                        "document" in data or "documents" in data):
                    self._send(422, {"error": "invalid_plan_source_selection"})
                    return
                if creating and "documents" in data:
                    if (local_multi_document_analyzer is None
                            or local_agent_plan_store.multi_document_analyzer is None):
                        self._send(403, {"error": "plan_multi_documents_disabled"})
                        return
                    try:
                        source_set_snapshot = validated_document_set_snapshot(
                            data["documents"], data["model"])
                        source_set_metadata = [
                            {k: item[k] for k in ("filename", "source_bytes", "sha256")}
                            for item in document_set_source_metadata(source_set_snapshot)]
                    except PlanError as exc:
                        self._send(422, {"error": str(exc)})
                        return
                if creating and "document" in data:
                    if (local_document_analyzer is None
                            or local_agent_plan_store.document_analyzer is None):
                        self._send(403, {"error": "plan_documents_disabled"})
                        return
                    try:
                        source_snapshot = validated_document_snapshot(
                            data["document"], data["model"])
                        source_metadata = document_source_metadata(source_snapshot)
                        source_metadata = {k: source_metadata[k] for k in
                                           ("filename", "source_bytes", "sha256")}
                    except PlanError as exc:
                        self._send(422, {"error": str(exc)})
                        return
                if creating and "csv" in data:
                    if local_agent_plan_store.csv_profiler is None:
                        self._send(403, {"error": "plan_csv_disabled"})
                        return
                    try:
                        csv_snapshot = validated_csv_snapshot(data["csv"])
                        csv_metadata = {key: value for key, value in
                            csv_source_metadata(csv_snapshot).items()
                            if key in ("filename", "source_bytes", "sha256",
                                       "row_count", "columns")}
                    except PlanError as exc:
                        self._send(422, {"error": str(exc)})
                        return
                if self.path == "/api/local-agent-plan-export":
                    try:
                        require_owner_action(
                            MARKDOWN_ACTION_TOOL,
                            enabled=(plan_artifact_exporter is not None))
                    except ToolPermissionError:
                        self._send(403, {"error": "plan_artifact_export_disabled"})
                        return
                    if not self._audit_event("LOCAL_ARTIFACT_EXPORT", "ATTEMPT"):
                        self._send(503, {"error": "security_audit_restart_required"})
                        return
                    try:
                        snapshot = local_agent_plan_store.get(data["plan_id"])
                        # Never accept a filename, directory or report body
                        # through HTTP. The server owns both the source and sink.
                        result = plan_artifact_exporter.export(snapshot)
                        if not self._audit_event("LOCAL_ARTIFACT_EXPORT", "SUCCESS"):
                            # File may exist. A retry is idempotent, never overwrite.
                            self._send(503, {"error": "artifact_write_outcome_uncertain"})
                        else:
                            self._send(200, result)
                    except PlanError as exc:
                        self._send(404 if str(exc) == "plan_not_found" else
                                   422 if str(exc).startswith("invalid_") else 503,
                                   {"error": str(exc)})
                    except PlanArtifactError as exc:
                        err = str(exc)
                        self._send(409 if err in ("artifact_requires_completed_plan",
                                                  "artifact_requires_saved_report",
                                                  "artifact_target_conflict",
                                                  "artifact_report_invalid") else
                                   422 if err == "artifact_too_large" else 503,
                                   {"error": err})
                    return
                if self.path in ("/api/local-agent-plan-batch-start",
                                 "/api/local-agent-plan-batch-stop"):
                    try:
                        outcome = (batch_runner.start(data["plan_id"])
                                   if self.path.endswith("batch-start")
                                   else batch_runner.stop(data["plan_id"]))
                        self._send(200, outcome)
                    except PlanError as exc:
                        err = str(exc)
                        self._send(429 if err == "local_agent_busy" else
                                   422 if err.startswith("invalid_") else
                                   404 if err == "plan_not_found" else
                                   409 if err in ("plan_state_conflict",
                                                  "memory_context_changed",
                                                  "plan_batch_active") else 503,
                                   {"error": err})
                    return
                if not self._enter_agent_gate(allow_uncertain=(not creating and
                        self.path not in ("/api/local-agent-plan-next",
                                          "/api/local-agent-plan-report"))):
                    return
                try:
                    try:
                        if creating:
                            if len(local_agent_plan_store.list()) >= MAX_PLANS:
                                raise PlanError("plan_capacity_reached")
                            kwargs = {}
                            refs = None
                            if selected_memory_ids is not None:
                                cards = agent_memory_store.selected_with_revisions(
                                    selected_memory_ids)
                                kwargs["memory_context"] = [
                                    {"id": c["id"], "text": c["text"]} for c in cards]
                                refs = [{"id": c["id"], "revision": c["revision"],
                                         "digest": c["digest"]} for c in cards]
                            if source_metadata is not None:
                                kwargs["document_context"] = source_metadata
                            if source_set_metadata is not None:
                                kwargs["document_set_context"] = source_set_metadata
                            if csv_metadata is not None:
                                kwargs["csv_context"] = csv_metadata
                            if hn_policy is not None:
                                kwargs["hn_allowed_ids"] = hn_policy.snapshot()[
                                    "allowed_story_ids"]
                            proposal = local_agent_planner(
                                data["goal"], data["model"], **kwargs)
                            # The plan store independently validates the exact
                            # owner-supplied two-field source. The transport's
                            # enriched (hashed) metadata must not be passed as
                            # though it were the raw input contract.
                            outcome = local_agent_plan_store.create(
                                proposal, memory_refs=refs,
                                document_snapshot=(data["document"]
                                                   if source_snapshot is not None
                                                   else None),
                                document_set_snapshot=(data["documents"]
                                                       if source_set_snapshot is not None
                                                       else None),
                                csv_snapshot=(data["csv"] if csv_snapshot is not None
                                              else None))
                        else:
                            action = {
                                "/api/local-agent-plan-approve": local_agent_plan_store.approve,
                                "/api/local-agent-plan-next": local_agent_plan_store.advance,
                                "/api/local-agent-plan-cancel": local_agent_plan_store.cancel,
                                "/api/local-agent-plan-resume": local_agent_plan_store.resume_interrupted,
                                "/api/local-agent-plan-delete": local_agent_plan_store.delete,
                                "/api/local-agent-plan-report": local_agent_plan_store.synthesize_report,
                            }[self.path]
                            outcome = action(data["plan_id"])
                        self._send(200, outcome)
                    except MemoryCardError as exc:
                        err = str(exc)
                        self._send(422 if err.startswith("invalid_") else
                                   404 if err == "memory_card_not_found" else 503,
                                   {"error": err})
                    except PlanError as exc:
                        self._mark_model_uncertainty(exc)
                        err = str(exc)
                        code = (422 if err.startswith("invalid_") or err == "tool_not_allowed"
                                else 403 if err == "public_source_not_allowed"
                                else 404 if err == "plan_not_found"
                                else 409 if err in ("plan_state_conflict", "plan_capacity_reached",
                                                     "public_source_permission_changed",
                                                     "memory_context_changed",
                                                     "plan_report_requires_completed",
                                                     "plan_handoff_invalid_source")
                                else 503)
                        self._send(code, {"error": err})
                    except AgentError as exc:
                        self._mark_model_uncertainty(exc)
                        self._send(503, {"error": str(exc)})
                    except Exception:
                        self._send(503, {"error": "agent_plan_unavailable"})
                finally:
                    self._leave_agent_gate()
                return
            if self.path == "/api/local-agent-probe":
                if local_agent_runner is None or local_agent_probe is None:
                    self._send(403, {"error": "local_agent_probe_disabled"})
                    return
                if type(data) is not dict or set(data) != {"model"} or type(data["model"]) is not str:
                    self._send(422, {"error": "invalid_probe_request"})
                    return
                if not self._enter_agent_gate():
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
                        self._mark_model_uncertainty(exc)
                        code = (422 if str(exc).startswith("invalid_") else
                                409 if str(exc) == "model_not_installed" else 503)
                        self._send(code, {"error": str(exc)})
                    except Exception:
                        self._send(503, {"error": "local_model_probe_failed"})
                finally:
                    self._leave_agent_gate()
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
                if not self._enter_agent_gate(allow_uncertain=True):
                    return
                try:
                    agent_conversation.clear(selected_id)
                    self._send(200, {"status": "CLEARED_LOCAL",
                                     "conversation_id": selected_id, "turns": []})
                except ConversationError:
                    self._send(503, {"error": "conversation_storage_unavailable"})
                finally:
                    self._leave_agent_gate()
                return
            if self.path == "/api/local-agent-run":
                if local_agent_runner is None:
                    self._send(403, {"error": "local_agent_disabled"})
                    return
                if (type(data) is not dict
                        or not {"model", "prompt"}.issubset(data)
                        or not set(data).issubset(
                            {"model", "prompt", "remember", "conversation_id", "memory_ids"})
                        or type(data["model"]) is not str or type(data["prompt"]) is not str
                        or ("remember" in data and type(data["remember"]) is not bool)):

                    self._send(422, {"error": "invalid_local_agent_request"})
                    return
                memory_ids = data.get("memory_ids")
                if "memory_ids" in data:
                    if not valid_selection(memory_ids):
                        self._send(422, {"error": "invalid_memory_selection"})
                        return
                    if agent_memory_store is None:
                        self._send(403, {"error": "agent_memory_disabled"})
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
                if not self._enter_agent_gate():
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
                        selected_cards = (agent_memory_store.selected(memory_ids)
                                          if memory_ids is not None else None)
                        kwargs = {}
                        if remember:
                            kwargs["history"] = prior
                        if selected_cards is not None:
                            kwargs["memory_context"] = selected_cards
                        outcome = local_agent_runner(data["prompt"], data["model"], **kwargs)
                        if type(outcome) is not dict or outcome.get("mode") != "LOCAL_MODEL":
                            self._send(502, {"error": "invalid_agent_result"})
                        else:
                            if remember:
                                agent_conversation.append(
                                    data["model"], data["prompt"], outcome.get("answer"),
                                    conversation_id)
                            self._send(200, {**outcome, "remembered": remember,
                                             "conversation_id": conversation_id
                                             if remember else None,
                                             "memory_ids_used": memory_ids or [],
                                             "memory_source": (
                                                 "OWNER_CURATED_UNVERIFIED"
                                                 if memory_ids else None)})
                    except MemoryCardError as exc:
                        err = str(exc)
                        self._send(422 if err.startswith("invalid_") else
                                   404 if err == "memory_card_not_found" else 503,
                                   {"error": err})
                    except ConversationError as exc:
                        code = 409 if str(exc) == "conversation_capacity_reached" else 503
                        self._send(code, {"error": str(exc)})
                    except AgentError as exc:
                        self._mark_model_uncertainty(exc)
                        self._send(422 if str(exc).startswith("invalid_") else 503,
                                   {"error": str(exc)})
                    except Exception:
                        self._send(503, {"error": "local_agent_unavailable"})
                finally:
                    self._leave_agent_gate()
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
            if self.path == "/api/local-greenhouse-job-preview":
                keys = {"board_token", "job_id"}
                if (type(data) is not dict or
                        set(data) not in (keys, keys | {"hn_story_id"})):
                    self._send(422, {"error": "invalid_greenhouse_evidence_request"})
                    return
                try:
                    validate_greenhouse_target(
                        data["board_token"], data["job_id"],
                        data.get("hn_story_id"))
                except GreenhouseEvidenceError as exc:
                    self._send(422, {"error": str(exc)})
                    return
                try:
                    require_discovery_preview(
                        GREENHOUSE_EVIDENCE,
                        enabled=(greenhouse_evidence is not None
                                 and local_agent_planner is not None
                                 and local_agent_plan_store is not None))
                except ToolPermissionError:
                    self._send(403, {"error": "greenhouse_evidence_disabled"})
                    return
                if not greenhouse_check_gate.acquire(blocking=False):
                    self._send(429, {"error": "greenhouse_evidence_busy"})
                    return
                try:
                    if not self._audit_event("GREENHOUSE_JOB_CHECK", "ATTEMPT"):
                        self._send(503, {"error": "security_audit_restart_required"})
                        return
                    try:
                        result = greenhouse_evidence.preview(
                            data["board_token"], data["job_id"],
                            data.get("hn_story_id"))
                    except GreenhouseEvidenceError as exc:
                        if not self._audit_event("GREENHOUSE_JOB_CHECK", "FAILED"):
                            self._send(503, {"error": "security_audit_restart_required"})
                        else:
                            err = str(exc)
                            self._send(
                                404 if err == "greenhouse_job_not_published_or_not_found"
                                else 502 if err.startswith("greenhouse_provider")
                                or err == "greenhouse_response_size_rejected"
                                else 422,
                                {"error": err})
                        return
                    except Exception:
                        self._audit_event("GREENHOUSE_JOB_CHECK", "FAILED")
                        self._send(503, {"error": "greenhouse_evidence_unavailable"})
                        return
                    if (type(result) is not dict
                            or result.get("source") !=
                               "GREENHOUSE_OFFICIAL_PUBLIC_JOB_BOARD_V1"
                            or result.get("job_id") != data["job_id"]
                            or result.get("board_token") != data["board_token"]
                            or result.get("public_job_post_observed") is not True
                            or result.get("indonesia_eligibility_verified") is not False
                            or result.get("hn_to_ats_match_verified") is not False
                            or type(result.get("eligibility_triage")) is not dict
                            or result["eligibility_triage"].get("version") !=
                               "ATS_PUBLIC_ELIGIBILITY_TRIAGE_V1"
                            or result["eligibility_triage"].get(
                                "candidate_work_location") != "Indonesia"
                            or result["eligibility_triage"].get(
                                "indonesia_candidate_verdict") not in (
                                "MANUAL_REVIEW_REQUIRED",
                                "POSTING_LOCATION_CONFLICT_FOR_INDONESIA")
                            or result["eligibility_triage"].get(
                                "source_is_public_employer_claim_not_legally_verified") is not True
                            or result["eligibility_triage"].get(
                                "no_auto_apply_or_filter") is not True
                            or result.get("external_read_count") != 1
                            or result.get("external_write_count") != 0
                            or result.get("model_calls") != 0
                            or result.get("local_file_write_count") != 0
                            or result.get("applicant_data_sent") is not False):
                        self._audit_event("GREENHOUSE_JOB_CHECK", "FAILED")
                        self._send(502, {"error": "invalid_greenhouse_evidence_output"})
                        return
                    if not self._audit_event("GREENHOUSE_JOB_CHECK", "SUCCESS"):
                        self._send(503, {"error": "security_audit_restart_required"})
                        return
                    self._send(200, result)
                finally:
                    greenhouse_check_gate.release()
                return
            if self.path == "/api/local-hn-jobstories-preview":
                if type(data) is not dict or data:
                    self._send(422, {"error":"invalid_hn_discovery_request"})
                    return
                try:
                    require_discovery_preview(
                        HN_DISCOVERY_PREVIEW,
                        enabled=(hn_job_discovery is not None
                                 and hn_policy is not None
                                 and local_agent_planner is not None
                                 and local_agent_plan_store is not None
                                 and local_agent_plan_store.hn_reader is not None))
                except ToolPermissionError:
                    self._send(403, {"error":"hn_discovery_permission_denied"})
                    return
                if not hn_discovery_gate.acquire(blocking=False):
                    self._send(429, {"error":"hn_discovery_busy"})
                    return
                try:
                    if not self._audit_event("HN_JOB_DISCOVERY", "ATTEMPT"):
                        self._send(503, {"error":"security_audit_restart_required"})
                        return
                    try:
                        preview = hn_job_discovery.preview()
                    except HNDiscoveryError as exc:
                        if not self._audit_event("HN_JOB_DISCOVERY", "FAILED"):
                            self._send(503, {"error":"security_audit_restart_required"})
                        else:
                            err = str(exc)
                            self._send(502 if err.startswith(
                                "hn_discovery_provider") or err ==
                                "hn_discovery_item_unavailable" else 503,
                                {"error":err})
                        return
                    except Exception:
                        self._audit_event("HN_JOB_DISCOVERY", "FAILED")
                        self._send(503, {"error":"hn_discovery_unavailable"})
                        return
                    if (type(preview) is not dict
                            or preview.get("source") !=
                               "HACKER_NEWS_PUBLIC_JOBSTORIES_PREVIEW_V1"
                            or preview.get("external_write_count") != 0
                            or preview.get("auto_grant_count") != 0
                            or preview.get("model_calls") != 0
                            or preview.get("local_file_write_count") != 0
                            or preview.get("requires_separate_owner_grant") is not True
                            or type(preview.get("jobs")) is not list
                            or len(preview["jobs"]) > 5):
                        self._audit_event("HN_JOB_DISCOVERY", "FAILED")
                        self._send(502, {"error":"invalid_hn_discovery_output"})
                        return
                    if not self._audit_event("HN_JOB_DISCOVERY", "SUCCESS"):
                        self._send(503, {"error":"security_audit_restart_required"})
                        return
                    self._send(200, preview)
                finally:
                    hn_discovery_gate.release()
                return
            if self.path in ("/api/local-hn-story-grant",
                             "/api/local-hn-story-revoke"):
                if (type(data) is not dict or
                        set(data) != {"story_id", "current_password"} or
                        not valid_story_id(data.get("story_id")) or
                        type(data.get("current_password")) is not str):
                    self._send(422, {"error":"invalid_hn_grant_request"})
                    return
                if hn_policy is None or operator_auth is None:
                    self._send(403, {"error":"hn_story_read_disabled"})
                    return
                action = ("HN_STORY_GRANT" if self.path.endswith("grant")
                          else "HN_STORY_REVOKE")
                if not self._audit_event(action, "ATTEMPT"):
                    self._send(503, {"error":"security_audit_restart_required"})
                    return
                approved = operator_auth.confirm_current_password(
                    data["current_password"])
                if approved != 200:
                    self._audit_event(action, "DENIED")
                    self._send(approved, {"error": (
                        "operator_restart_required" if approved == 503
                        else "operator_login_locked" if approved == 429
                        else "invalid_operator_credentials")})
                    return
                method = hn_policy.grant if self.path.endswith("grant") else hn_policy.revoke
                code, outcome = method(data["story_id"])
                end = ("SUCCESS" if code == 200 else
                       "UNCERTAIN" if outcome.get("error") == "hn_policy_restart_required"
                       else "DENIED" if code in (401,403,404,409,422) else "FAILED")
                if not self._audit_event(action, end):
                    self._send(503, {"error":"security_audit_restart_required"})
                    return
                self._send(code, outcome)
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
            if self.path in ("/api/autopilot-preview",
                             "/api/autopilot-arm",
                             "/api/autopilot-stop"):
                if scheduled_autopilot is None:
                    self._send(403, {"error": "autopilot_not_configured"})
                    return
                if not self._audit_event("WEBHOOK_AUTOPILOT_POLICY", "ATTEMPT"):
                    self._send(503, {"error": "security_audit_restart_required"})
                    return
                try:
                    if self.path == "/api/autopilot-preview":
                        response = scheduled_autopilot.preview(data)
                        status = 200
                    elif self.path == "/api/autopilot-arm":
                        if (type(data) is not dict or
                                set(data) != {"schedule_id", "max_sends",
                                             "expires_days", "preview_token",
                                             "current_password"} or
                                type(data["current_password"]) is not str):
                            raise AutopilotError("invalid_autopilot_arm_fields")
                        if operator_auth is None:
                            raise AutopilotError("real_owner_required",403)
                        verified = operator_auth.confirm_current_password(
                            data["current_password"])
                        if verified != 200:
                            self._audit_event("WEBHOOK_AUTOPILOT_POLICY", "DENIED")
                            self._send(verified, {"error": (
                                "operator_restart_required" if verified == 503
                                else "operator_login_locked" if verified == 429
                                else "invalid_operator_credentials")})
                            return
                        response = scheduled_autopilot.arm(
                            {k:v for k,v in data.items() if k != "current_password"})
                        status = 201
                    else:
                        if (type(data) is not dict or
                                set(data) != {"schedule_id", "expected_epoch"}):
                            raise AutopilotError("invalid_autopilot_stop_fields")
                        response = scheduled_autopilot.stop(
                            data["schedule_id"], data["expected_epoch"])
                        status = 200
                except AutopilotError as exc:
                    self._audit_event("WEBHOOK_AUTOPILOT_POLICY", "DENIED")
                    self._send(exc.status, {"error": str(exc)})
                    return
                except (sqlite3.Error, OSError):
                    self._audit_event("WEBHOOK_AUTOPILOT_POLICY", "UNCERTAIN")
                    self._send(503, {"error": "autopilot_state_uncertain_check_history"})
                    return
                if not self._audit_event("WEBHOOK_AUTOPILOT_POLICY", "SUCCESS"):
                    self._send(503, {"error": "autopilot_policy_result_uncertain_check_status"})
                else:
                    self._send(status, response)
                return
            if self.path in ("/api/outbound-webhook-preview",
                             "/api/outbound-webhook-commit"):
                if outbound_webhook is None:
                    self._send(403, {"error": "outbound_webhook_disabled"})
                    return
                committing = self.path.endswith("-commit")
                if not self._audit_event("WEBHOOK_DELIVERY", "ATTEMPT"):
                    self._send(503, {"error": "security_audit_restart_required"})
                    return
                try:
                    if committing:
                        if (type(data) is not dict or
                                set(data) != {"workflow_id", "event_id",
                                             "preview_token", "payload_sha256",
                                             "current_password"} or
                                type(data["current_password"]) is not str):
                            raise OutboundError("invalid_webhook_confirmation")
                        if operator_auth is None:
                            raise OutboundError("real_owner_required", 403)
                        verified = operator_auth.confirm_current_password(
                            data["current_password"])
                        if verified != 200:
                            self._audit_event("WEBHOOK_DELIVERY", "DENIED")
                            self._send(verified, {"error": (
                                "operator_restart_required" if verified == 503
                                else "operator_login_locked" if verified == 429
                                else "invalid_operator_credentials")})
                            return
                        safe_body = {k: v for k, v in data.items()
                                     if k != "current_password"}
                        result = outbound_webhook.commit(safe_body)
                        code = 200
                    else:
                        result = outbound_webhook.preview(data)
                        code = 200
                except OutboundError as exc:
                    self._audit_event("WEBHOOK_DELIVERY", "DENIED")
                    self._send(exc.status, {"error": str(exc)})
                    return
                except (sqlite3.Error, OSError):
                    self._audit_event("WEBHOOK_DELIVERY", "UNCERTAIN")
                    self._send(503, {"error": "webhook_delivery_state_unavailable_check_ledger"})
                    return
                outcome = ("SUCCESS" if not committing else
                           "SUCCESS" if result["status"] == "REMOTE_ACCEPTED" else
                           "UNCERTAIN")
                if not self._audit_event("WEBHOOK_DELIVERY", outcome):
                    self._send(503, {"error": "webhook_delivery_result_uncertain_check_history"})
                else:
                    self._send(code, result)
                return
            if self.path in ("/api/local-schedule-create",
                             "/api/local-schedule-pause",
                             "/api/local-schedule-resume"):
                if local_scheduler is None:
                    self._send(403, {"error": "recurring_scheduler_disabled"})
                    return
                if not self._audit_event("SCHEDULE_OWNER_CHANGE", "ATTEMPT"):
                    self._send(503, {"error": "security_audit_restart_required"})
                    return
                try:
                    if self.path == "/api/local-schedule-create":
                        result = local_scheduler.create(data)
                        code = 201
                    else:
                        if (type(data) is not dict or set(data) !=
                                {"schedule_id", "expected_epoch"}):
                            raise ScheduleError("invalid_schedule_control_fields")
                        result = local_scheduler.change(
                            data["schedule_id"], data["expected_epoch"],
                            pause=self.path.endswith("-pause"))
                        code = 200
                except ScheduleError as exc:
                    self._audit_event("SCHEDULE_OWNER_CHANGE", "DENIED")
                    self._send(exc.status, {"error": str(exc)})
                    return
                except (sqlite3.Error, OSError):
                    self._send(503, {"error": "scheduler_storage_unavailable"})
                    return
                if not self._audit_event("SCHEDULE_OWNER_CHANGE", "SUCCESS"):
                    self._send(503, {"error": "scheduler_change_outcome_uncertain"})
                else:
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
                       enable_multi_documents=False,
                       enable_agent_memory=False,
                       enable_plan_artifacts=False,
                       enable_plan_csv=False, enable_plan_actions=False,
                       enable_hn_story_read=False,
                       enable_hn_job_discovery=False,
                       enable_greenhouse_evidence=False,
                       enable_outbound_webhook=False):
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
    if enable_agent_memory and not enable_local_agent:
        raise ValueError("agent_memory_requires_local_agent")
    if enable_local_documents and not enable_local_agent:
        raise ValueError("local_documents_require_local_agent")
    if enable_multi_documents and not enable_local_agent:
        raise ValueError("multi_documents_require_local_agent")
    if enable_agent_plans and not enable_local_agent:
        raise ValueError("agent_plans_require_local_agent")
    if enable_agent_github_read and not enable_agent_plans:
        raise ValueError("github_plan_read_requires_agent_plans")
    if enable_hn_story_read and not enable_agent_plans:
        raise ValueError("hn_story_read_requires_agent_plans")
    if enable_hn_job_discovery and not enable_hn_story_read:
        raise ValueError("hn_job_discovery_requires_hn_story_read")
    if enable_greenhouse_evidence and not enable_agent_plans:
        raise ValueError("greenhouse_evidence_requires_agent_plans")
    if enable_plan_artifacts and not enable_agent_plans:
        raise ValueError("plan_artifacts_require_agent_plans")
    if enable_plan_csv and not enable_agent_plans:
        raise ValueError("plan_csv_requires_agent_plans")
    if enable_plan_actions and (not enable_agent_plans
                                or not enable_plan_csv
                                or not enable_plan_artifacts):
        raise ValueError("plan_actions_require_plans_csv_and_artifacts")
    conversation = None
    plan_store = None
    memory_store = None
    hn_policy = (HNStoryPolicy(home / "hn-story-grants.json")
                 if enable_hn_story_read else None)
    if enable_agent_memory:
        memory_store = LocalAgentMemory(home / "agent-memory-cards.sqlite3")
    if enable_agent_plans:
        reader = (AllowedGitHubIssuesReader(source_policy)
                  if enable_agent_github_read else None)
        hn_reader = AllowedHNStoryReader(hn_policy) if hn_policy is not None else None
        plan_store = LocalAgentPlanStore(
            home / "agent-task-plans.sqlite3", github_reader=reader,
            hn_reader=hn_reader,
            memory_store=memory_store,
            document_analyzer=(analyze_local_document
                               if enable_local_documents else None),
            multi_document_analyzer=(analyze_multiple_documents
                                     if enable_multi_documents else None),
            csv_profiler=(profile_selected_csv if enable_plan_csv else None))
    if enable_agent_history and enable_local_agent:
        conversation = LocalAgentConversation(home / "agent-conversation.sqlite3")
    model_safety = (LocalModelSafetyState(home / "ollama-safety.sqlite3")
                    if enable_local_agent else None)
    scheduler = None
    outbound = None
    autopilot = None
    try:
        if enable_outbound_webhook:
            destination = os.environ.get("AAS_WEBHOOK_URL", "")
            if not destination:
                raise OutboundError("webhook_url_required_at_process_start")
            outbound = LocalOutboundWebhook(
                core, home / "outbound-webhook-ledger.sqlite3",
                url=destination,
                secret=os.environ.get("AAS_WEBHOOK_SIGNING_SECRET", ""))
        scheduler = LocalRecurringScheduler(core, home / "recurring-schedules.sqlite3")
        if outbound is not None:
            autopilot = ScheduledWebhookAutopilot(
                scheduler,outbound,audit,home / "scheduled-webhook-autopilot.sqlite3")
            scheduler.outbound_autopilot = autopilot
        server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            create_handler(local_core=core, operator_auth=operator,
                           source_policy=source_policy, security_audit=audit,
                           hn_policy=hn_policy,
                           hn_job_discovery=(
                               HNJobDiscovery() if enable_hn_job_discovery else None),
                           greenhouse_evidence=(
                               GreenhouseJobEvidence()
                               if enable_greenhouse_evidence else None),
                           local_agent_runner=run_agent if enable_local_agent else None,
                           agent_conversation=conversation,
                           local_agent_models=installed_models if enable_local_agent else None,
                           local_agent_probe=probe_model if enable_local_agent else None,
                           local_agent_planner=(
                               (lambda goal, model, memory_context=None, document_context=None,
                                       document_set_context=None, csv_context=None,
                                       hn_allowed_ids=None:
                                propose_readonly_plan(
                                   goal, model, allow_github=enable_agent_github_read,
                                   memory_context=memory_context,
                                   document_context=document_context,
                                   document_set_context=document_set_context,
                                   csv_context=csv_context,
                                   allow_hn=enable_hn_story_read,
                                   hn_allowed_ids=hn_allowed_ids))
                               if enable_agent_plans else None),
                           local_agent_plan_store=plan_store,
                           local_scheduler=scheduler,
                           outbound_webhook=outbound,
                           scheduled_autopilot=autopilot,
                           local_document_analyzer=(
                               analyze_local_document if enable_local_documents else None),
                           local_multi_document_analyzer=(
                               analyze_multiple_documents if enable_multi_documents else None),
                           local_device_acceptance=(
                               run_local_device_acceptance if enable_multi_documents else None),
                           local_device_preflight=(
                               check_local_device_preflight if enable_multi_documents else None),
                           model_safety_state=model_safety,
                           agent_memory_store=memory_store,
                           plan_artifact_exporter=(
                               LocalPlanArtifactExporter(home / "exports")
                               if enable_plan_artifacts else None),
                           plan_action_gate=(
                               LocalCSVProfileActionGate(
                                   plan_store, home / "exports", audit)
                               if enable_plan_actions else None)))
        print(f"Local AI Automation Studio: http://127.0.0.1:{server.server_port}")
        scheduler.start()
        try:
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        if scheduler is not None:
            scheduler.close()
        if autopilot is not None:
            autopilot.close()
        if outbound is not None:
            outbound.close()
        if memory_store is not None:
            memory_store.close()
        if model_safety is not None:
            model_safety.close()
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
    parser.add_argument("--enable-agent-memory", action="store_true",
                        help="Opt in to owner-curated plaintext local memory cards.")
    parser.add_argument("--enable-agent-history", action="store_true",
                        help="Explicit opt-in local plaintext transcript storage.")
    parser.add_argument("--enable-agent-plans", action="store_true",
                        help="Opt in to persistent, human-approved read-only task plans.")
    parser.add_argument("--enable-multi-documents", action="store_true",
                        help="Opt in to 2-3 manually selected text files with bounded retrieval.")
    parser.add_argument("--enable-local-documents", action="store_true",
                        help="Separately opt into owner-selected, in-memory text file analysis.")
    parser.add_argument("--enable-plan-csv", action="store_true",
                        help="Opt in to a single owner-selected, short CSV snapshot for reviewed deterministic profile steps.")
    parser.add_argument("--enable-plan-actions", action="store_true",
                        help="Opt in to separate preview and one-use owner-confirmed create-only CSV profile JSON exports; also requires --enable-plan-csv and --enable-plan-artifacts.")
    parser.add_argument("--enable-plan-artifacts", action="store_true",
                        help="Allow explicit, write-once Markdown report export to the local owner workspace.")
    parser.add_argument("--enable-greenhouse-evidence", action="store_true",
                        help="Opt in to exact owner-clicked public Greenhouse job-post evidence checks. Requires reviewed agent plans.")
    parser.add_argument("--enable-hn-job-discovery", action="store_true",
                        help="Opt in to separate owner-clicked official public HN jobstories preview (max 5 titles, no grants). Requires --enable-hn-story-read.")
    parser.add_argument("--enable-hn-story-read", action="store_true",
                        help="Opt in to per-story owner-approved public Hacker News story/job thread reads (max 2 comments).")
    parser.add_argument("--enable-outbound-webhook", action="store_true",
                        help="Explicitly enable one owner-approved HTTPS webhook destination from AAS_WEBHOOK_URL environment. Never automatically send or retry.")
    parser.add_argument("--enable-agent-github-read", action="store_true",
                        help="Separately opt in to operator-allowlisted public GitHub issue reads in plans.")
    args = parser.parse_args(argv)
    if args.enable_agent_memory and not args.enable_local_agent:
        parser.error("--enable-agent-memory requires --enable-local-agent")
    if args.enable_local_documents and not args.enable_local_agent:
        parser.error("--enable-local-documents requires --enable-local-agent")
    if args.enable_multi_documents and not args.enable_local_agent:
        parser.error("--enable-multi-documents requires --enable-local-agent")
    if args.enable_agent_plans and not args.enable_local_agent:
        parser.error("--enable-agent-plans requires --enable-local-agent")
    if args.enable_agent_github_read and not args.enable_agent_plans:
        parser.error("--enable-agent-github-read requires --enable-agent-plans")
    if args.enable_hn_story_read and not args.enable_agent_plans:
        parser.error("--enable-hn-story-read requires --enable-agent-plans")
    if args.enable_hn_job_discovery and not args.enable_hn_story_read:
        parser.error("--enable-hn-job-discovery requires --enable-hn-story-read")
    if args.enable_greenhouse_evidence and not args.enable_agent_plans:
        parser.error("--enable-greenhouse-evidence requires --enable-agent-plans")
    if args.enable_plan_artifacts and not args.enable_agent_plans:
        parser.error("--enable-plan-artifacts requires --enable-agent-plans")
    if args.enable_plan_csv and not args.enable_agent_plans:
        parser.error("--enable-plan-csv requires --enable-agent-plans")
    if args.enable_plan_actions and (not args.enable_agent_plans
                                     or not args.enable_plan_csv
                                     or not args.enable_plan_artifacts):
        parser.error("--enable-plan-actions requires --enable-agent-plans --enable-plan-csv and --enable-plan-artifacts")
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
                           enable_agent_memory=args.enable_agent_memory,
                           enable_agent_plans=args.enable_agent_plans,
                           enable_agent_github_read=args.enable_agent_github_read,
                           enable_local_documents=args.enable_local_documents,
                           enable_multi_documents=args.enable_multi_documents,
                           enable_plan_artifacts=args.enable_plan_artifacts,
                           enable_plan_csv=args.enable_plan_csv,
                           enable_plan_actions=args.enable_plan_actions,
                           enable_hn_story_read=args.enable_hn_story_read,
                           enable_hn_job_discovery=args.enable_hn_job_discovery,
                           enable_greenhouse_evidence=args.enable_greenhouse_evidence,
                           enable_outbound_webhook=args.enable_outbound_webhook)
    except (OperatorConfigurationError, SourcePolicyError, SecurityAuditError, OutboundError) as exc:
        raise SystemExit(
            str(exc) + ": run python app/local_server.py --init-owner first.")


if __name__ == "__main__":
    main()
