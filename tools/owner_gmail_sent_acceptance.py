"""Owner-safe localhost CLI for Stage 120 Gmail SENT metadata corroboration.

Only localhost Studio can call Gmail using the separately encrypted OAuth
grant. This client never accepts a remote URL, prints raw message headers,
stores passwords, or retries an uncertain provider read.
"""
import argparse
import getpass
import json
import re
import sys

from owner_live_n8n_acceptance import OwnerLiveClient, OwnerLiveAcceptanceError

PLAN = re.compile(r"^[0-9a-f]{24}$", re.ASCII)
CONFIRM = "VERIFY_GMAIL_SENT_ARTIFACT_ONCE"


def _report(status, attempted=False):
    return {
        "status": status,
        "owner_cli": "STAGE_120_LOCALHOST_GMAIL_SENT_V1",
        "remote_gmail_read_may_have_occurred": bool(attempted),
        "gmail_sent_artifact_verified": status == "SENT_ARTIFACT_MATCHED",
        "causal_link_to_n8n_verified": False,
        "recipient_delivery_verified": False,
        "business_effect_verified": False,
        "external_write_count": 0,
        "automatic_retry": False,
    }


class OwnerGmailSentClient(OwnerLiveClient):
    def execute(self, *, mode, plan_id, password, confirmation=None,
                gmail_message_id=None, expected_to=None, expected_subject=None):
        if (mode not in ("inspect","verify") or
                type(plan_id) is not str or not PLAN.fullmatch(plan_id)):
            raise OwnerLiveAcceptanceError("invalid_gmail_acceptance_request")
        attempted=False
        try:
            self._login(password)
            checks=self._request("/api/n8n-execution-check-history")
            if type(checks.get("records")) is not list:
                raise OwnerLiveAcceptanceError("n8n_history_missing")
            found=[x for x in checks["records"]
                   if type(x) is dict and x.get("plan_id") == plan_id]
            if (len(found)!=1 or found[0].get("status") != "EXECUTION_SUCCESS_CORRELATED"
                    or found[0].get("inbound_delivery_correlated") is not True):
                raise OwnerLiveAcceptanceError("correlated_n8n_success_required")
            caps=self._request("/api/local-agent-capabilities")
            if caps.get("gmail_sent_artifact_verification") is not True:
                raise OwnerLiveAcceptanceError("gmail_evidence_feature_disabled")
            if caps.get("signed_gmail_artifact_binding_required") is True:
                bound=self._request("/api/signed-gmail-artifact-bindings")
                claims=bound.get("records")
                if (type(claims) is not list
                        or sum(type(x) is dict and x.get("plan_id")==plan_id
                               for x in claims) != 1):
                    raise OwnerLiveAcceptanceError("signed_gmail_binding_preflight_required")
            elif caps.get("signed_gmail_artifact_binding_required") is not False:
                raise OwnerLiveAcceptanceError("gmail_binding_policy_unknown")
            previous=self._request("/api/gmail-sent-evidence-history")
            if type(previous.get("records")) is not list:
                raise OwnerLiveAcceptanceError("gmail_history_missing")
            if any(type(x) is dict and x.get("plan_id")==plan_id
                   for x in previous["records"]):
                raise OwnerLiveAcceptanceError("already_checked_check_history")
            if mode=="inspect":
                return _report("READY_FOR_ONE_GMAIL_SENT_METADATA_READ")
            if confirmation!=CONFIRM:
                raise OwnerLiveAcceptanceError("explicit_gmail_consent_required")
            if not all(type(x) is str and x for x in
                       (gmail_message_id,expected_to,expected_subject)):
                raise OwnerLiveAcceptanceError("gmail_reference_required")
            attempted=True
            try:
                result=self._request("/api/gmail-sent-evidence",{
                    "plan_id":plan_id, "gmail_message_id":gmail_message_id,
                    "expected_to":expected_to,
                    "expected_subject":expected_subject,
                    "current_password":password, "consent":CONFIRM,
                })
            except OwnerLiveAcceptanceError:
                return _report("LOCAL_RESPONSE_UNCERTAIN_NO_RETRY",attempted=True)
            if (result.get("plan_id")!=plan_id
                    or result.get("external_write_count")!=0
                    or result.get("causal_link_to_n8n_verified") is not False
                    or result.get("recipient_delivery_verified") is not False
                    or result.get("business_effect_verified") is not False):
                return _report("EVIDENCE_RESPONSE_CONTRACT_FAILED",attempted=True)
            status=result.get("status")
            if status in ("SENT_ARTIFACT_MATCHED","NOT_CORRELATED",
                          "LOOKUP_UNCERTAIN"):
                return _report(status,attempted=True)
            return _report("EVIDENCE_RESPONSE_CONTRACT_FAILED",attempted=True)
        except OwnerLiveAcceptanceError:
            if attempted:
                return _report("LOCAL_RESPONSE_UNCERTAIN_NO_RETRY",attempted=True)
            raise
        finally:
            self._logout()


def main(argv=None):
    parser=argparse.ArgumentParser(description=(
        "Read only: compare one Gmail SENT metadata artifact with owner-supplied "
        "recipient/subject AFTER n8n execution is correlated. Never sends mail."))
    parser.add_argument("mode",choices=["inspect","verify"])
    parser.add_argument("--plan-id",required=True)
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args(argv)
    try:
        client=OwnerGmailSentClient(port=args.port)
        password=getpass.getpass("Studio owner password (hidden): ")
        inputs={}
        if args.mode=="verify":
            inputs["confirmation"]=input("Type "+CONFIRM+" to approve: ").strip()
            inputs["gmail_message_id"]=getpass.getpass(
                "Gmail SENT message ID (hidden): ")
            inputs["expected_to"]=getpass.getpass(
                "Expected single recipient email (hidden): ")
            inputs["expected_subject"]=getpass.getpass(
                "Expected exact subject (hidden): ")
        result=client.execute(
            mode=args.mode,plan_id=args.plan_id,password=password,**inputs)
    except (OwnerLiveAcceptanceError,ValueError,EOFError,KeyboardInterrupt):
        result=_report("PREFLIGHT_BLOCKED")
    print(json.dumps(result,sort_keys=True))
    return 0 if result["status"] in (
        "READY_FOR_ONE_GMAIL_SENT_METADATA_READ",
        "SENT_ARTIFACT_MATCHED") else 2


if __name__=="__main__":
    sys.exit(main())
