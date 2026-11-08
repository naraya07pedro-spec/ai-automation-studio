"""Stage 122 owner-run localhost official n8n Gmail Send output acceptance.

Inspect performs only owner-authenticated localhost reads. Verify authorizes
ONE extra official n8n execution GET through Studio, never Gmail send, retries,
arbitrary provider URLs or secret exporting.
"""
import argparse
import getpass
import json
import re
import sys

from owner_live_n8n_acceptance import OwnerLiveClient, OwnerLiveAcceptanceError

PLAN = re.compile(r"^[0-9a-f]{24}$",re.ASCII)
CONFIRM = "VERIFY_ONE_N8N_GMAIL_SEND_NODE_GET"


def _report(status, attempted=False):
    return {
        "status":status,
        "mode":"OWNER_OFFICIAL_N8N_GMAIL_OUTPUT_V1",
        "provider_get_may_have_occurred":bool(attempted),
        "official_n8n_gmail_output_correlated":status=="PROVIDER_SEND_NODE_ID_CORRELATED",
        "recipient_delivery_verified":False,
        "causal_business_effect_verified":False,
        "external_write_count":0,
        "automatic_retry":False,
    }


class OwnerN8NGmailOutputClient(OwnerLiveClient):
    def execute(self, *, mode, plan_id, password, confirmation=None):
        if (mode not in ("inspect","verify") or
                type(plan_id) is not str or PLAN.fullmatch(plan_id) is None):
            raise OwnerLiveAcceptanceError("invalid_gmail_output_acceptance_request")
        attempted=False
        try:
            self._login(password)
            caps=self._request("/api/local-agent-capabilities")
            if (caps.get("n8n_execution_readonly_verify") is not True or
                    caps.get("signed_gmail_artifact_binding_required") is not True or
                    caps.get("official_n8n_gmail_output_required") is not True):
                raise OwnerLiveAcceptanceError("strict_official_gmail_output_not_enabled")
            n8n=self._request("/api/n8n-execution-check-history")
            rows=n8n.get("records")
            if (type(rows) is not list or
                    sum(type(x) is dict and x.get("plan_id")==plan_id and
                        x.get("status")=="EXECUTION_SUCCESS_CORRELATED" and
                        x.get("inbound_delivery_correlated") is True
                        for x in rows)!=1):
                raise OwnerLiveAcceptanceError("correlated_n8n_execution_required")
            signed=self._request("/api/signed-gmail-artifact-bindings")
            rows=signed.get("records")
            if (type(rows) is not list or
                    sum(type(x) is dict and x.get("plan_id")==plan_id
                        for x in rows)!=1):
                raise OwnerLiveAcceptanceError("signed_gmail_artifact_required")
            prev=self._request("/api/n8n-gmail-output-witness-history")
            rows=prev.get("records")
            if type(rows) is not list:
                raise OwnerLiveAcceptanceError("n8n_gmail_witness_history_missing")
            if any(type(x) is dict and x.get("plan_id")==plan_id
                   for x in rows):
                raise OwnerLiveAcceptanceError("n8n_gmail_witness_already_checked")
            if mode=="inspect":
                return _report("READY_FOR_ONE_OFFICIAL_N8N_GMAIL_OUTPUT_READ")
            if confirmation != CONFIRM:
                raise OwnerLiveAcceptanceError("explicit_n8n_gmail_output_consent_required")
            attempted=True
            try:
                result=self._request("/api/n8n-gmail-output-witness",{
                    "plan_id":plan_id,"current_password":password,"consent":CONFIRM})
            except OwnerLiveAcceptanceError:
                return _report("LOCAL_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY",True)
            if (result.get("plan_id")!=plan_id
                    or result.get("external_write_count")!=0
                    or result.get("recipient_delivery_verified") is not False
                    or result.get("causal_business_effect_verified") is not False
                    or result.get("automatic_retry") is not False):
                return _report("RESPONSE_CONTRACT_NOT_PROVEN",True)
            status=result.get("status")
            if status in ("PROVIDER_SEND_NODE_ID_CORRELATED",
                          "PROVIDER_SEND_NODE_NOT_CORRELATED","LOOKUP_UNCERTAIN"):
                return _report(status,True)
            return _report("RESPONSE_CONTRACT_NOT_PROVEN",True)
        except OwnerLiveAcceptanceError:
            if attempted:
                return _report("LOCAL_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY",True)
            raise
        finally:
            self._logout()


def main(argv=None):
    p=argparse.ArgumentParser(description=(
        "One owner-authorized read-only official n8n execution GET; verify "
        "pinned Gmail Send node ID, no messages sent."))
    p.add_argument("mode",choices=["inspect","verify"])
    p.add_argument("--plan-id",required=True)
    p.add_argument("--port",type=int,default=8765)
    args=p.parse_args(argv)
    try:
        password=getpass.getpass("Studio owner password (hidden): ")
        consent=(input("Type "+CONFIRM+" to approve: ").strip()
                 if args.mode=="verify" else None)
        report=OwnerN8NGmailOutputClient(port=args.port).execute(
            mode=args.mode,plan_id=args.plan_id,
            password=password,confirmation=consent)
    except (OwnerLiveAcceptanceError,EOFError,KeyboardInterrupt,ValueError):
        report=_report("PREFLIGHT_BLOCKED")
    print(json.dumps(report,sort_keys=True))
    return 0 if report["status"] in (
        "READY_FOR_ONE_OFFICIAL_N8N_GMAIL_OUTPUT_READ",
        "PROVIDER_SEND_NODE_ID_CORRELATED") else 2


if __name__=="__main__":
    sys.exit(main())
