"""Loopback-only, standard-library local product demo for synthetic inquiries.

Not a production HTTP server. In-memory deduplication resets on process restart.
No authentication, live provider integrations, externally reachable host,
credentials, outbound sends, or customer data persistence.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "prototype"))
from lead_inquiry_flow import MockInquiryFlow
from lead_inquiry_demo_review import LocalMockReview, execution_timeline

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


def create_handler(flow=None):
    """Return an isolated handler class for tests or local app instantiation."""
    workflow = flow if flow is not None else MockInquiryFlow()
    lock = threading.Lock()
    review = LocalMockReview()

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

        def do_GET(self):
            if self.path == "/":
                self._send(200, WEB.read_text(encoding="utf-8"),
                           "text/html; charset=utf-8")
            elif self.path == "/api/health":
                self._send(200, {"status": "MOCK_ONLY", "external_dispatch_count": 0})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self):
            if self.path not in ("/api/mock-run", "/api/mock-review"):
                self._send(404, {"error": "not_found"})
                return
            # Reject cross-origin mutation attempts. This is still a local
            # development demo, not an authentication or public CSRF solution.
            host = self.headers.get("Host", "")
            if host not in ("127.0.0.1", "localhost",
                            f"127.0.0.1:{self.server.server_port}",
                            f"localhost:{self.server.server_port}"):
                self._send(403, {"error": "local_host_required"})
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin not in (
                    f"http://127.0.0.1:{self.server.server_port}",
                    f"http://localhost:{self.server.server_port}"):
                self._send(403, {"error": "same_origin_required"})
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
            if self.path == "/api/mock-review":
                if (type(data) is not dict or set(data) != {"review_token", "decision"}):
                    self._send(422, {"error": "invalid_review_request"})
                    return
                with lock:
                    result = review.decide(data["review_token"], data["decision"])
                self._send(200, {"mode": "MOCK_ONLY", "review": result,
                                 "external_dispatch_count": 0})
                return
            if (type(data) is not dict
                    or set(data) != {"event_id", "service_requested",
                                     "budget_range", "timeline"}
                    or any(type(v) is not str or not v.strip() or len(v) > 120
                           for v in data.values())
                    or len(data["event_id"]) > 80):
                self._send(422, {"error": "invalid_synthetic_fields"})
                return
            event = {
                "source_event_id": data["event_id"],
                "inquiry": {key: data[key] for key in _REQUIRED},
            }
            # Shared process-local store must not be accessed simultaneously.
            with lock:
                result = workflow.process(_CONFIGURATION, event, _REQUIRED)
                offer = review.offer(result)
            self._send(200, {"mode": "MOCK_ONLY", "result": result,
                             "timeline": execution_timeline(result),
                             "review_offer": offer,
                             "external_dispatch_count": 0})

    return Handler


def start_local_server(port=8765):
    """Bind only IPv4 loopback; never expose this unauthenticated demo to LAN."""
    server = ThreadingHTTPServer(("127.0.0.1", port), create_handler())
    print(f"Mock-only AI Automation Studio: http://127.0.0.1:{server.server_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    start_local_server()
