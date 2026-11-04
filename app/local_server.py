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
import sys
import threading
from urllib.parse import parse_qs, urlsplit
from local_workflow_core import LocalWorkflowCore
from local_operator_auth import LocalOperatorAuth, OperatorConfigurationError
from local_source_policy import LocalPublicSourcePolicy, SourcePolicyError
from local_csv_batch import LocalCSVBatchIntake
from github_public_issue_intake import PublicGitHubReadOnlyIntake

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
                   operator_auth=None, source_policy=None):
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
                                 "/api/mock-run", "/api/mock-review", "/api/mock-setup", "/api/mock-triage", "/api/mock-appointment", "/api/mock-crm-handoff", "/api/mock-knowledge-routing", "/api/local-create", "/api/local-activate", "/api/local-run", "/api/local-batch-preview", "/api/local-batch-commit", "/api/local-stage", "/api/local-publish", "/api/local-pause", "/api/local-resume", "/api/local-github-preview", "/api/local-github-commit"):
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
            if self.path == "/api/operator-login":
                if operator_auth is None:
                    self._send(403, {"error": "auth_fixture_only"})
                    return
                if type(data) is not dict or set(data) != {"password"}:
                    self._send(422, {"error": "invalid_login_fields"})
                    return
                status, session = operator_auth.login(data["password"])
                if status != 200:
                    self._send(status, {"error": (
                        "operator_login_locked" if status == 429
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
                code, outcome = operator_auth.rotate_password(
                    data["current_password"], data["new_password"])
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
                approved = operator_auth.confirm_current_password(
                    data["current_password"])
                if approved != 200:
                    self._send(approved, {"error": (
                        "operator_login_locked" if approved == 429
                        else "invalid_operator_credentials")})
                    return
                operation = (source_policy.grant if self.path.endswith("grant")
                             else source_policy.revoke)
                code, outcome = operation(data["repo"])
                self._send(code, outcome)
                return
            if self.path == "/api/operator-logout":
                if type(data) is not dict or data:
                    self._send(422, {"error": "invalid_logout_fields"})
                    return
                if operator_auth is not None:
                    operator_auth.logout(self._operator_token())
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


def start_local_server(port=8765):
    """Refuse to bind until one offline owner verifier is provisioned."""
    home = Path.home() / ".ai-automation-studio"
    operator = LocalOperatorAuth.load(home / "operator.json")
    source_policy = LocalPublicSourcePolicy(home / "public-github-sources.json")
    core = LocalWorkflowCore(home / "local-workflows.sqlite3")
    try:
        server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            create_handler(local_core=core, operator_auth=operator,
                           source_policy=source_policy))
        print(f"Local AI Automation Studio: http://127.0.0.1:{server.server_port}")
        try:
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        core.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Local-only workflow server with offline owner provisioning.")
    parser.add_argument("--init-owner", action="store_true",
                        help="Provision a single operator password offline.")
    args = parser.parse_args(argv)
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
        start_local_server()
    except (OperatorConfigurationError, SourcePolicyError) as exc:
        raise SystemExit(
            str(exc) + ": run python app/local_server.py --init-owner first.")


if __name__ == "__main__":
    main()
