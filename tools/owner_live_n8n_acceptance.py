"""Stage 119: owner-run live n8n acceptance through the existing localhost server.

This is a small authenticated HTTP client, not a separate n8n API caller.
PRECHECK makes only localhost reads after login. VERIFY performs at most
ONE explicit owner-approved POST to the server's existing read-only n8n
execution verifier, never an n8n write or a webhook retry.

No provider key, password, execution response, plan contents, event details,
webhook URL, or individual identifiers are printed or written to files.
"""
import argparse
from http.cookiejar import CookieJar
import getpass
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)

PLAN = re.compile(r"^[0-9a-f]{24}$", re.ASCII)
EXECUTION = re.compile(r"^[1-9][0-9]{0,15}$", re.ASCII)
HOST = re.compile(r"^[A-Za-z0-9.-]{4,253}$", re.ASCII)
WORKFLOW = re.compile(r"^[A-Za-z0-9_-]{1,128}$", re.ASCII)
MAX_RESPONSE = 65536
CONFIRMATION = "VERIFY_ONE_REMOTE_N8N_GET"


class OwnerLiveAcceptanceError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise OwnerLiveAcceptanceError("unexpected_local_redirect")


def _safe_report(status, *, attempted=False, verified=False, outcome=None):
    result = {
        "status":status,
        "client_mode":"OWNER_APPROVED_LOCALHOST_N8N_ACCEPTANCE_V1",
        "signed_receipt_preflight":status not in (
            "PREFLIGHT_BLOCKED", "LOCAL_SERVER_UNAVAILABLE"),
        "provider_read_may_have_been_dispatched":bool(attempted),
        "live_provider_execution_correlated":bool(verified),
        "provider_execution_outcome":outcome if outcome in ("success","error") else None,
        "live_provider_verified_by_this_client":bool(verified),
        "business_effect_verified":False,
        "new_webhook_sends":0,
        "automatic_retry":False,
        "raw_provider_response_exported":False,
    }
    return result


class OwnerLiveClient:
    """Never accepts a remote host, URL or path from user input."""

    def __init__(self, *, port=8765):
        if type(port) is not int or not 1 <= port <= 65535:
            raise OwnerLiveAcceptanceError("invalid_local_port")
        self.origin = "http://127.0.0.1:" + str(port)
        self.cookies = CookieJar()
        self.opener = build_opener(
            ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(self.cookies))
        self.csrf = None

    def _request(self, path, values=None):
        if path not in (
            "/api/operator-login", "/api/operator-logout",
            "/api/local-agent-capabilities", "/api/signed-handoff-outcomes",
            "/api/n8n-execution-check-history", "/api/n8n-execution-check",
            "/api/gmail-sent-evidence-history", "/api/gmail-sent-evidence",
            "/api/signed-gmail-artifact-bindings", "/api/signed-gmail-artifact-import",
            "/api/n8n-gmail-output-witness-history", "/api/n8n-gmail-output-witness",
        ):
            raise OwnerLiveAcceptanceError("local_endpoint_not_allowed")
        headers = {"Accept":"application/json"}
        body = None
        if values is not None:
            headers["Content-Type"] = "application/json"
            if path != "/api/operator-login":
                if not self.csrf:
                    raise OwnerLiveAcceptanceError("operator_session_missing")
                headers["X-AAS-CSRF"] = self.csrf
            body = json.dumps(values, ensure_ascii=True,
                separators=(",", ":")).encode("ascii")
        request = Request(self.origin + path, data=body,
                          headers=headers,
                          method="POST" if values is not None else "GET")
        try:
            with self.opener.open(request, timeout=30 if
                                  path=="/api/gmail-sent-evidence" else 18 if
                                  path=="/api/n8n-execution-check" else 5) as response:
                if response.status != 200:
                    raise OwnerLiveAcceptanceError("local_http_not_successful")
                # Never expose large/hostile application responses.
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise OwnerLiveAcceptanceError("local_json_response_too_large")
                parsed = json.loads(raw.decode("utf-8"))
                if type(parsed) is not dict:
                    raise ValueError("expected_object")
                return parsed
        except OwnerLiveAcceptanceError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError,
                UnicodeError, json.JSONDecodeError):
            # Do not leak HTTP bodies, cookie/password, or backend exception.
            raise OwnerLiveAcceptanceError(
                "local_response_unavailable_or_uncertain") from None

    def _login(self, password):
        if type(password) is not str or not password:
            raise OwnerLiveAcceptanceError("owner_password_required")
        reply=self._request("/api/operator-login",{"password":password})
        csrf=reply.get("csrf_token")
        if (reply.get("authenticated") is not True
                or type(csrf) is not str or not 16 <= len(csrf) <= 256):
            raise OwnerLiveAcceptanceError("real_owner_session_required")
        self.csrf=csrf

    def _logout(self):
        if self.csrf:
            try:
                self._request("/api/operator-logout",{})
            except OwnerLiveAcceptanceError:
                pass
            self.csrf=None
        self.cookies.clear()

    @staticmethod
    def _validate(plan_id, expected_host, expected_workflow,
                  expected_node, expected_claim):
        if type(plan_id) is not str or not PLAN.fullmatch(plan_id):
            raise OwnerLiveAcceptanceError("invalid_plan_id")
        if (type(expected_host) is not str or not HOST.fullmatch(expected_host)
                or "." not in expected_host):
            raise OwnerLiveAcceptanceError("invalid_expected_host")
        if (type(expected_workflow) is not str or
                not WORKFLOW.fullmatch(expected_workflow)):
            raise OwnerLiveAcceptanceError("invalid_expected_workflow")
        if (type(expected_node) is not str or
                not 1 <= len(expected_node) <= 100 or
                any(ord(ch) < 32 or ord(ch)==127 for ch in expected_node)):
            raise OwnerLiveAcceptanceError("invalid_expected_node")
        if expected_claim not in ("COMPLETED","FAILED"):
            raise OwnerLiveAcceptanceError("invalid_expected_claim")

    def _preflight(self, plan_id, expected_host, expected_workflow,
                   expected_node, expected_claim):
        caps=self._request("/api/local-agent-capabilities")
        if (caps.get("n8n_execution_readonly_verify") is not True
                or caps.get("agent_connector_handoff") is not True
                or caps.get("signed_handoff_outcome_import") is not True):
            raise OwnerLiveAcceptanceError("n8n_verifier_not_enabled")
        if (caps.get("n8n_execution_hostname") != expected_host.lower()
                or caps.get("n8n_pinned_workflow_id") != expected_workflow
                or caps.get("n8n_pinned_webhook_node") != expected_node):
            raise OwnerLiveAcceptanceError("n8n_target_pin_mismatch")
        signed=self._request("/api/signed-handoff-outcomes")
        claims=signed.get("records")
        if type(claims) is not list:
            raise OwnerLiveAcceptanceError("signed_receipt_history_unavailable")
        found=[x for x in claims if type(x) is dict and x.get("plan_id")==plan_id]
        if len(found)!=1:
            raise OwnerLiveAcceptanceError("signed_receipt_not_in_recent_history")
        receipt=found[0]
        if (receipt.get("signature_valid") is not True or
                receipt.get("provider_asserted_outcome") != expected_claim or
                type(receipt.get("n8n_execution_id")) is not str or
                not EXECUTION.fullmatch(receipt["n8n_execution_id"]) or
                receipt.get("independently_verified_remote") is not False or
                receipt.get("business_effect_verified") is not False or
                receipt.get("no_automatic_retry") is not True):
            raise OwnerLiveAcceptanceError("signed_receipt_unsafe_or_unexpected")
        history=self._request("/api/n8n-execution-check-history")
        previous=history.get("records")
        if type(previous) is not list:
            raise OwnerLiveAcceptanceError("provider_check_history_unavailable")
        if any(type(x) is dict and x.get("plan_id")==plan_id for x in previous):
            raise OwnerLiveAcceptanceError("provider_check_already_recorded")
        return receipt

    def execute(self, *, mode, plan_id, expected_host, expected_workflow,
                expected_node, expected_claim, password, confirmation=None):
        """Caller handles terminal prompts. All errors redact sensitive details."""
        self._validate(plan_id, expected_host, expected_workflow,
                       expected_node, expected_claim)
        if mode not in ("inspect","verify"):
            raise OwnerLiveAcceptanceError("invalid_acceptance_mode")
        attempted=False
        try:
            self._login(password)
            receipt=self._preflight(plan_id, expected_host,
                expected_workflow, expected_node, expected_claim)
            if mode=="inspect":
                return _safe_report("READY_FOR_ONE_OWNER_APPROVED_N8N_READ")
            # Both explicit typed consent and fresh password are required.
            if confirmation != CONFIRMATION:
                raise OwnerLiveAcceptanceError("explicit_n8n_read_consent_required")
            attempted=True
            try:
                result=self._request("/api/n8n-execution-check",{
                    "plan_id":plan_id, "current_password":password,
                })
            except OwnerLiveAcceptanceError:
                # POST may have crossed the local server boundary even if
                # the socket breaks. NEVER automatically reissue it.
                return _safe_report(
                    "LOCAL_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY",
                    attempted=True)
            if (result.get("delivery_id") != receipt.get("delivery_id")
                    or result.get("execution_id") != receipt.get("n8n_execution_id")
                    or result.get("provider_hostname") != expected_host.lower()
                    or result.get("configured_n8n_workflow_id") != expected_workflow
                    or result.get("external_write_count") != 0
                    or result.get("business_effect_verified") is not False
                    or result.get("automatic_retry") is not False
                    or result.get("remote_reads") != 1):
                return _safe_report("PROVIDER_RESPONSE_CONTRACT_NOT_PROVEN",
                                    attempted=True)
            status=result.get("status")
            if (status=="EXECUTION_SUCCESS_CORRELATED"
                    and result.get("provider_execution_status")=="success"
                    and result.get("inbound_delivery_correlated") is True
                    and result.get("provider_execution_verified_with_delivery") is True):
                return _safe_report("N8N_EXECUTION_SUCCESS_CORRELATED",
                                    attempted=True,verified=True,outcome="success")
            if (status=="EXECUTION_FAILED_CORRELATED"
                    and result.get("provider_execution_status")=="error"
                    and result.get("inbound_delivery_correlated") is True
                    and result.get("provider_execution_verified_with_delivery") is True):
                return _safe_report("N8N_EXECUTION_FAILED_CORRELATED",
                                    attempted=True,verified=True,outcome="error")
            return _safe_report("N8N_EXECUTION_NOT_CORRELATED_OR_CONFLICTING",
                                attempted=True)
        except OwnerLiveAcceptanceError as exc:
            if attempted:
                return _safe_report(
                    "LOCAL_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY",
                    attempted=True)
            raise
        finally:
            self._logout()


def main(argv=None):
    parser=argparse.ArgumentParser(
        description="Owner-approved localhost Stage 119 n8n execution acceptance. "
        "No raw provider response or secrets are printed.")
    parser.add_argument("mode",choices=["inspect","verify"])
    parser.add_argument("--plan-id",required=True)
    parser.add_argument("--expected-host",required=True)
    parser.add_argument("--expected-workflow",required=True)
    parser.add_argument("--expected-node",required=True)
    parser.add_argument("--expected-claim",required=True,
                        choices=["COMPLETED","FAILED"])
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args(argv)
    try:
        client=OwnerLiveClient(port=args.port)
        # Do not expose passwords in command lines, files, or terminal echoes.
        password=getpass.getpass("Current Studio owner password (hidden): ")
        confirmation=None
        if args.mode=="verify":
            print("About to authorize ONE n8n execution GET using the already "
                  "configured localhost Studio. No webhook POST or retry.",
                  file=sys.stderr)
            confirmation=input("Type "+CONFIRMATION+" to approve: ").strip()
        report=client.execute(
            mode=args.mode,plan_id=args.plan_id,
            expected_host=args.expected_host,
            expected_workflow=args.expected_workflow,
            expected_node=args.expected_node,
            expected_claim=args.expected_claim,
            password=password,confirmation=confirmation)
    except (OwnerLiveAcceptanceError,EOFError,KeyboardInterrupt):
        report=_safe_report("PREFLIGHT_BLOCKED")
    print(json.dumps(report,sort_keys=True))
    # A confirmed FAILED provider execution is useful evidence, but must NOT
    # accidentally look like a successful task to Windows/shell automation.
    return 0 if report["status"] in (
        "READY_FOR_ONE_OWNER_APPROVED_N8N_READ",
        "N8N_EXECUTION_SUCCESS_CORRELATED") else 2


if __name__=="__main__":
    sys.exit(main())
