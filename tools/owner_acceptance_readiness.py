"""Stage 125: private, read-only owner acceptance progress navigator.

Reads only existing authenticated localhost Studio history. It NEVER calls
n8n, Gmail, a paid model, a connector dispatch, or any provider-write API.
This is a navigation aid, NOT a verifier of stored hashes or real delivery.
"""
import argparse
import getpass
import json
import re
import sys

from owner_live_n8n_acceptance import OwnerLiveClient, OwnerLiveAcceptanceError

PLAN = re.compile(r"^[0-9a-f]{24}$", re.ASCII)
HISTORY_ENDPOINTS = (
    ("signed_handoff", "/api/signed-handoff-outcomes"),
    ("n8n_execution", "/api/n8n-execution-check-history"),
    ("signed_gmail", "/api/signed-gmail-artifact-bindings"),
    ("n8n_gmail_output", "/api/n8n-gmail-output-witness-history"),
    ("gmail_sent", "/api/gmail-sent-evidence-history"),
)
REQUIRED_CAPABILITIES = (
    "agent_connector_handoff",
    "signed_handoff_outcome_import",
    "n8n_execution_readonly_verify",
    "signed_gmail_artifact_binding_required",
    "official_n8n_gmail_output_required",
    "gmail_sent_artifact_verification",
)
STAGES = tuple(key for key, _ in HISTORY_ENDPOINTS)
NEXT_ACTIONS = {
    "signed_handoff": "REVIEW_EXISTING_SIGNED_HANDOFF_AND_DISPATCH_HISTORY",
    "n8n_execution": "INSPECT_OWNER_LIVE_N8N_ACCEPTANCE",
    "signed_gmail": "INSPECT_OWNER_SIGNED_GMAIL_IMPORT",
    "n8n_gmail_output": "INSPECT_OWNER_N8N_GMAIL_OUTPUT_ACCEPTANCE",
    "gmail_sent": "INSPECT_OWNER_GMAIL_SENT_ACCEPTANCE",
}


def _report(status, next_action, stages=None):
    return {
        "stage": 125,
        "status": status,
        "next_action": next_action,
        "history_visibility": "CAPPED_RECENT_RECORDS_ONLY",
        "stages": stages if stages is not None else {name: "UNREAD" for name in STAGES},
        "saved_record_integrity_audited": False,
        "provider_contacted_by_this_command": False,
        "external_write_count": 0,
        "automatic_retry": False,
        "real_recipient_delivery_verified": False,
        "business_effect_verified": False,
        "off_device_restore_verified": False,
        "production_ready": False,
    }


def evaluate(capabilities, histories, plan_id):
    """Conservative decision from existing capped local records; never infer absence."""
    if type(capabilities) is not dict or any(
        capabilities.get(key) is not True for key in REQUIRED_CAPABILITIES
    ):
        return _report("CONFIGURATION_BLOCKED", "ENABLE_STRICT_LOCAL_ACCEPTANCE_FEATURES")
    stages = {name: "UNREAD" for name in STAGES}
    evidence = {}
    for name in STAGES:
        page = histories.get(name)
        if type(page) is not dict or type(page.get("records")) is not list:
            return _report("HISTORY_UNAVAILABLE", "REVIEW_LOCAL_HISTORY_MANUALLY", stages)
        matching = [row for row in page["records"]
                    if type(row) is dict and row.get("plan_id") == plan_id]
        if len(matching) > 1:
            stages[name] = "CONFLICT"
            return _report("CONFLICT_STOP_NO_RETRY", "REVIEW_LOCAL_HISTORY_MANUALLY", stages)
        evidence[name] = matching[0] if matching else None
        stages[name] = "VISIBLE" if matching else "NOT_VISIBLE_IN_RECENT_HISTORY"

    validators = {
        "signed_handoff": lambda r: (
            r.get("signature_valid") is True and
            r.get("provider_asserted_outcome") == "COMPLETED"),
        "n8n_execution": lambda r: (
            r.get("status") == "EXECUTION_SUCCESS_CORRELATED" and
            r.get("provider_execution_status") == "success" and
            r.get("provider_finished") is True and
            r.get("inbound_delivery_correlated") is True),
        "signed_gmail": lambda r: (
            r.get("signature_valid_at_import") is True and
            r.get("provider_n8n_execution_correlated_at_import") is True),
        "n8n_gmail_output": lambda r: (
            r.get("status") == "PROVIDER_SEND_NODE_ID_CORRELATED"),
        "gmail_sent": lambda r: (
            r.get("status") == "SENT_ARTIFACT_MATCHED" and
            r.get("gmail_sent_label_seen") is True),
    }
    for name in STAGES:
        row = evidence[name]
        if row is None:
            return _report("NEXT_LOCAL_ACCEPTANCE_STEP", NEXT_ACTIONS[name], stages)
        if not validators[name](row):
            stages[name] = "CONFLICT"
            return _report("CONFLICT_STOP_NO_RETRY", "REVIEW_LOCAL_HISTORY_MANUALLY", stages)
        stages[name] = "SAVED_SUCCESS_REPORTED"
    return _report("SAVED_CHAIN_PRESENT_RUN_OFFLINE_AUDIT",
                   "STOP_STUDIO_AND_RUN_STRICT_NINE_LAYER_OFFLINE_AUDIT", stages)


def inspect_saved_chain(plan_id, password, *, port=8765, client=None):
    if type(plan_id) is not str or PLAN.fullmatch(plan_id) is None:
        raise OwnerLiveAcceptanceError("invalid_plan_id")
    if type(password) is not str or not password:
        raise OwnerLiveAcceptanceError("owner_password_required")
    client = client if client is not None else OwnerLiveClient(port=port)
    try:
        client._login(password)
        capabilities = client._request("/api/local-agent-capabilities")
        # No optional inputs, POST/verify endpoint, credentials or plan metadata.
        histories = {name: client._request(path) for name, path in HISTORY_ENDPOINTS}
        return evaluate(capabilities, histories, plan_id)
    finally:
        client._logout()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only local progress: signed handoff -> n8n -> Gmail "
                    "identity -> n8n Send output -> Gmail SENT; no provider requests.")
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        password = getpass.getpass("Studio owner password (hidden): ")
        report = inspect_saved_chain(args.plan_id, password, port=args.port)
    except (OwnerLiveAcceptanceError, EOFError, KeyboardInterrupt, ValueError):
        report = _report("PREFLIGHT_BLOCKED", "CHECK_LOCAL_STUDIO_AUTH_AND_HISTORY")
    print(json.dumps(report, sort_keys=True))
    # Do not return zero for an absent, uncertain, incomplete or failed chain.
    return 0 if report["status"] == "SAVED_CHAIN_PRESENT_RUN_OFFLINE_AUDIT" else 2


if __name__ == "__main__":
    sys.exit(main())
