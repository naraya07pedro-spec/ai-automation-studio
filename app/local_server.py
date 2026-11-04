"""Loopback-only local demo and a minimal persistent, non-PII rule core.

Not a production HTTP server. No authentication, live providers or sends.
The separate local workbench stores pseudonymous event IDs and rule
categories in SQLite; mock workflow state still resets on restart.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import sys
import threading
from urllib.parse import parse_qs, urlsplit
from local_workflow_core import LocalWorkflowCore
from local_csv_batch import LocalCSVBatchIntake

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


def create_handler(flow=None, local_core=None):
    """Return an isolated handler class for tests or local app instantiation."""
    workflow = flow if flow is not None else MockInquiryFlow()
    lock = threading.Lock()
    review = LocalMockReview()
    triage = MockSupportTriage()
    appointments = MockAppointmentFollowup()
    crm = MockCRMHandoff()
    knowledge = MockKnowledgeRouting()
    core = local_core if local_core is not None else LocalWorkflowCore()
    batch = LocalCSVBatchIntake(core)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Refuse logging request paths, free text, identities and payloads.
            return

        def _send(self, code, payload, content_type="application/json; charset=utf-8"):
            body = (payload.encode("utf-8") if isinstance(payload, str)
                    else json.dumps(payload, sort_keys=True).encode("utf-8"))
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
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

        def do_GET(self):
            route = urlsplit(self.path)
            if route.path in ("/api/local-workflows", "/api/local-history"):
                denied = self._local_allowed()
                if denied:
                    self._send(403, {"error": denied})
                    return
                if route.path == "/api/local-workflows" and not route.query:
                    self._send(200, core.list_workflows())
                    return
                if route.path == "/api/local-history":
                    params = parse_qs(route.query, keep_blank_values=True)
                    if set(params) == {"workflow_id"} and len(params["workflow_id"]) == 1:
                        code, out = core.history(params["workflow_id"][0])
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
            if self.path not in ("/api/mock-run", "/api/mock-review", "/api/mock-setup", "/api/mock-triage", "/api/mock-appointment", "/api/mock-crm-handoff", "/api/mock-knowledge-routing", "/api/local-create", "/api/local-activate", "/api/local-run", "/api/local-batch-preview", "/api/local-batch-commit"):
                self._send(404, {"error": "not_found"})
                return
            # Reject cross-origin mutation attempts. This is still a local
            # development demo, not an authentication or public CSRF solution.
            denied = self._local_allowed()
            if denied:
                self._send(403, {"error": denied})
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
            if self.path in ("/api/local-batch-preview", "/api/local-batch-commit"):
                method = (batch.preview if self.path == "/api/local-batch-preview"
                          else batch.commit)
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


def start_local_server(port=8765):
    """Bind only IPv4 loopback; never expose this unauthenticated demo to LAN."""
    # The interactive local workbench persists only explicit non-PII
    # workflow IDs/rule categories. Other five demo workflows stay mock-only.
    core = LocalWorkflowCore(Path.home() / ".ai-automation-studio" / "local-workflows.sqlite3")
    server = ThreadingHTTPServer(("127.0.0.1", port), create_handler(local_core=core))
    print(f"Local AI Automation Studio: http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        core.close()


if __name__ == "__main__":
    start_local_server()
