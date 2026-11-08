"""Owner-only local Stage 121 signed Gmail artifact claim importer.

No n8n, Gmail, OpenAI, or webhook network calls. The existing authenticated
127.0.0.1 Studio server verifies the HMAC and saved Stage 117 execution.
"""
import argparse
import getpass
import json
import re
import sys

from owner_live_n8n_acceptance import OwnerLiveClient, OwnerLiveAcceptanceError

PLAN = re.compile(r"^[0-9a-f]{24}$",re.ASCII)
CONFIRM = "IMPORT_SIGNED_GMAIL_ARTIFACT_ONCE"
FIELDS = frozenset({
    "plan_id","connector_id","workflow_id","event_id","delivery_id",
    "execution_id","gmail_id_sha256","recipient_sha256","subject_sha256",
    "proof_hmac_sha256",
})


def _public(status, attempted=False):
    return {
        "status":status, "mode":"OWNER_LOCAL_SIGNED_GMAIL_IMPORT_V1",
        "local_import_may_have_occurred":bool(attempted),
        "new_remote_provider_requests":0,
        "new_webhook_sends":0,
        "gmail_sent_artifact_verified":False,
        "n8n_causation_verified":False,
        "recipient_delivery_verified":False,
        "business_effect_verified":False,
        "automatic_retry":False,
    }


class SignedGmailImportClient(OwnerLiveClient):
    def execute(self, *, mode, plan_id, password, claim_json=None,
                confirmation=None):
        if mode not in ("inspect","import") or type(plan_id) is not str or not PLAN.fullmatch(plan_id):
            raise OwnerLiveAcceptanceError("invalid_signed_gmail_import_request")
        attempted=False
        try:
            self._login(password)
            n8n=self._request("/api/n8n-execution-check-history")
            rows=n8n.get("records")
            if type(rows) is not list:
                raise OwnerLiveAcceptanceError("n8n_execution_history_not_available")
            matched=[x for x in rows if type(x) is dict and x.get("plan_id")==plan_id]
            if (len(matched)!=1 or matched[0].get("status")!="EXECUTION_SUCCESS_CORRELATED"
                    or matched[0].get("inbound_delivery_correlated") is not True):
                raise OwnerLiveAcceptanceError("successful_correlated_n8n_required")
            history=self._request("/api/signed-gmail-artifact-bindings")
            if type(history.get("records")) is not list:
                raise OwnerLiveAcceptanceError("signed_artifact_history_unavailable")
            if mode=="inspect":
                if any(type(x) is dict and x.get("plan_id")==plan_id
                       for x in history["records"]):
                    return _public("SIGNED_GMAIL_ARTIFACT_ALREADY_RECORDED")
                return _public("READY_FOR_OWNER_SIGNED_GMAIL_IMPORT")
            if confirmation != CONFIRM or type(claim_json) is not str or not 1<=len(claim_json)<=8192:
                raise OwnerLiveAcceptanceError("explicit_signed_gmail_import_consent_required")
            try:
                payload=json.loads(claim_json)
            except (ValueError,UnicodeError):
                raise OwnerLiveAcceptanceError("invalid_signed_claim_json") from None
            if (type(payload) is not dict or set(payload)!=FIELDS
                    or any(type(x) is not str for x in payload.values())
                    or payload.get("plan_id")!=plan_id):
                raise OwnerLiveAcceptanceError("invalid_signed_claim_fields")
            attempted=True
            try:
                result=self._request("/api/signed-gmail-artifact-import",{
                    **payload,"current_password":password,"consent":CONFIRM})
            except OwnerLiveAcceptanceError:
                return _public("LOCAL_IMPORT_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY",True)
            if (result.get("result") not in ("RECORDED","ALREADY_RECORDED")
                    or result.get("plan_id") != plan_id
                    or result.get("signature_valid_at_import") is not True
                    or result.get("business_effect_verified") is not False
                    or result.get("external_write_count") != 0):
                return _public("SIGNED_CLAIM_RESPONSE_CONTRACT_FAILED",True)
            return _public("SIGNED_GMAIL_ARTIFACT_BOUND",True)
        except OwnerLiveAcceptanceError:
            if attempted:
                return _public("LOCAL_IMPORT_RESPONSE_UNCERTAIN_CHECK_HISTORY_NO_RETRY",True)
            raise
        finally:
            self._logout()


def main(argv=None):
    parser=argparse.ArgumentParser(
        description="Import one HMAC-signed Gmail identity into private Studio only.")
    parser.add_argument("mode",choices=["inspect","import"])
    parser.add_argument("--plan-id",required=True)
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args(argv)
    try:
        client=SignedGmailImportClient(port=args.port)
        password=getpass.getpass("Current Studio owner password (hidden): ")
        kwargs={}
        if args.mode=="import":
            kwargs["confirmation"]=input("Type "+CONFIRM+" to approve: ").strip()
            kwargs["claim_json"]=getpass.getpass(
                "Paste exact signed n8n Gmail artifact JSON (hidden): ")
        result=client.execute(
            mode=args.mode,plan_id=args.plan_id,
            password=password,**kwargs)
    except (OwnerLiveAcceptanceError,KeyboardInterrupt,EOFError,ValueError):
        result=_public("PREFLIGHT_BLOCKED")
    print(json.dumps(result,sort_keys=True))
    return 0 if result["status"] in (
        "READY_FOR_OWNER_SIGNED_GMAIL_IMPORT",
        "SIGNED_GMAIL_ARTIFACT_ALREADY_RECORDED",
        "SIGNED_GMAIL_ARTIFACT_BOUND") else 2


if __name__ == "__main__":
    sys.exit(main())
