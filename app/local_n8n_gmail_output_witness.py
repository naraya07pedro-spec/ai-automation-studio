"""Stage 122: independently compare a pinned n8n Gmail Send node's output ID.

Owner-authorized one-shot GET of the SAME execution already correlated in
Stage 117. The execution's saved Gmail node type, send operation and exactly
one returned message ID must match the Stage 121 signed artifact binding.
No raw execution data, message ID, credentials or headers are persisted.
It does NOT verify recipient delivery or an actual business outcome.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import sqlite3

from local_agent_task_plans import ID_PATTERN as PLAN_ID
from local_gmail_sent_evidence import MESSAGE_ID
from local_n8n_execution_verifier import NODE, _inbound_binding
from local_signed_gmail_artifact import SignedGmailArtifactError

MAX_TOTAL = 100
MAX_PER_DAY = 5


class N8NGmailOutputError(RuntimeError):
    def __init__(self, reason, status=409):
        super().__init__(reason)
        self.status = status


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_send_node_message_id(response, node_name, execution_id, workflow_id):
    """Exact one-run, one-main-branch, one-item Gmail Send output only.

    Node identity is established by snapshot workflowData.nodes, never by
    node name alone, and neither arbitrary JSON paths nor multiple items are
    searched. Ambiguity fails closed.
    """
    if type(response) is not dict or not (
            str(response.get("id")) == execution_id
            and str(response.get("workflowId")) == workflow_id
            and response.get("status") == "success"
            and response.get("finished") is True):
        return None
    snapshot = response.get("workflowData")
    if type(snapshot) is not dict or type(snapshot.get("nodes")) is not list:
        return None
    nodes = [node for node in snapshot["nodes"]
             if type(node) is dict and node.get("name") == node_name]
    if len(nodes) != 1:
        return None
    node = nodes[0]
    params = node.get("parameters")
    if (node.get("type") != "n8n-nodes-base.gmail"
            or type(params) is not dict
            or params.get("resource") != "message"
            or params.get("operation") != "send"):
        return None
    data = response.get("data")
    if type(data) is not dict or type(data.get("resultData")) is not dict:
        return None
    run_data = data["resultData"].get("runData")
    if type(run_data) is not dict:
        return None
    runs = run_data.get(node_name)
    if type(runs) is not list or len(runs) != 1:
        return None
    run = runs[0]
    if (type(run) is not dict or run.get("error") is not None
            or type(run.get("data")) is not dict):
        return None
    branches = run["data"].get("main")
    if (type(branches) is not list or len(branches) != 1
            or type(branches[0]) is not list or len(branches[0]) != 1):
        return None
    item = branches[0][0]
    if type(item) is not dict or type(item.get("json")) is not dict:
        return None
    message_id = item["json"].get("id")
    if type(message_id) is not str or MESSAGE_ID.fullmatch(message_id) is None:
        return None
    return message_id


class N8NGmailOutputWitness:
    def __init__(self, n8n_verifier, signed_binding, *, gmail_node_name):
        if (n8n_verifier is None or signed_binding is None
                or signed_binding.n8n is not n8n_verifier
                or type(gmail_node_name) is not str
                or NODE.fullmatch(gmail_node_name) is None
                or gmail_node_name == n8n_verifier.node_name):
            raise N8NGmailOutputError("pinned_gmail_send_node_required",422)
        self.n8n = n8n_verifier
        self.binding = signed_binding
        self.node_name = gmail_node_name
        self.lock = n8n_verifier.handoff.lock
        self.db = n8n_verifier.handoff.db
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS n8n_gmail_output_witnesses(
                plan_id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                delivery_id TEXT NOT NULL,
                pinned_send_node_sha256 TEXT NOT NULL,
                signed_gmail_id_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                checked_at_utc TEXT NOT NULL,
                FOREIGN KEY(plan_id) REFERENCES agent_connector_handoffs(plan_id)
            )""")
            self.db.execute("""UPDATE n8n_gmail_output_witnesses
                SET status='LOOKUP_UNCERTAIN' WHERE status='IN_FLIGHT'""")

    def _chain(self, pid):
        try:
            signed = self.binding._correlation(pid)
            row = self.db.execute("""SELECT connector_id,delivery_id,execution_id,
                signed_message_sha256,gmail_id_sha256 FROM signed_gmail_artifact_bindings
                WHERE plan_id=?""",(pid,)).fetchone()
            if (row is None or row[:4] !=
                    (signed[1],signed[4],signed[5],signed[6])):
                raise N8NGmailOutputError("prior_signed_gmail_binding_required")
            return signed, row[4]
        except SignedGmailArtifactError as exc:
            raise N8NGmailOutputError(str(exc),exc.status) from None

    @staticmethod
    def _public(row):
        return {"plan_id":row[0],
                "n8n_execution_sha256":_sha(row[1]),
                "delivery_sha256":_sha(row[2]),
                "pinned_gmail_send_node_sha256":row[3],
                "signed_gmail_id_sha256":row[4],
                "status":row[5],"checked_at_utc":row[6],
                "official_n8n_gmail_send_output_id_correlated":
                    row[5]=="PROVIDER_SEND_NODE_ID_CORRELATED",
                "recipient_delivery_verified":False,
                "causal_business_effect_verified":False,
                "automatic_retry":False,
                "external_write_count":0}

    def verify_one(self, request):
        if (type(request) is not dict or set(request)!={"plan_id"}
                or type(request["plan_id"]) is not str
                or PLAN_ID.fullmatch(request["plan_id"]) is None):
            raise N8NGmailOutputError("invalid_n8n_gmail_witness_request",422)
        pid=request["plan_id"]
        with self.lock:
            try:
                signed, expected_hash = self._chain(pid)
                if self.db.execute("""SELECT 1 FROM gmail_sent_evidence
                       WHERE plan_id=?""",(pid,)).fetchone():
                    raise N8NGmailOutputError("gmail_already_checked_no_retroactive_witness")
                now=_now()
                # Serialize idempotency claims across process crash and
                # concurrent owner sessions, before contacting n8n.
                with self.db:
                    if self.db.execute("""SELECT 1 FROM n8n_gmail_output_witnesses
                            WHERE plan_id=?""",(pid,)).fetchone():
                        raise N8NGmailOutputError("n8n_gmail_witness_already_checked")
                    if self.db.execute("""SELECT COUNT(*) FROM n8n_gmail_output_witnesses
                            """).fetchone()[0]>=MAX_TOTAL:
                        raise N8NGmailOutputError("n8n_gmail_witness_capacity_reached")
                    if self.db.execute("""SELECT COUNT(*) FROM n8n_gmail_output_witnesses
                            WHERE checked_at_utc>=?""",(now[:10],)).fetchone()[0]>=MAX_PER_DAY:
                        raise N8NGmailOutputError("n8n_gmail_witness_daily_limit",429)
                    self.db.execute("""INSERT INTO n8n_gmail_output_witnesses(
                        plan_id,execution_id,delivery_id,pinned_send_node_sha256,
                        signed_gmail_id_sha256,status,checked_at_utc)
                        VALUES(?,?,?,?,?,'IN_FLIGHT',?)""",
                        (pid,signed[5],signed[4],_sha(self.node_name),
                         expected_hash,now))
            except sqlite3.Error:
                raise N8NGmailOutputError("n8n_gmail_witness_storage_unavailable",503) from None
        # One official execution GET on same verified host, using same
        # pre-provisioned API key and a no-redirect DNS-pinned transport.
        uncertain=False
        try:
            response=self.n8n._fetcher(self.n8n.host,signed[5],self.n8n._key)
            if (type(response) is not dict or "_api_error" in response):
                uncertain=True
                got=None
            elif not _inbound_binding(response,self.n8n.node_name,
                                      signed[2],signed[3],signed[4]):
                got=None
            else:
                got=extract_send_node_message_id(
                    response,self.node_name,signed[5],self.n8n.workflow_id)
        except Exception:
            uncertain=True
            got=None
        match = (not uncertain and got is not None and
                 hmac.compare_digest(_sha(got),expected_hash))
        status = ("LOOKUP_UNCERTAIN" if uncertain else
                  "PROVIDER_SEND_NODE_ID_CORRELATED" if match else
                  "PROVIDER_SEND_NODE_NOT_CORRELATED")
        with self.lock:
            try:
                with self.db:
                    changed=self.db.execute("""UPDATE n8n_gmail_output_witnesses
                        SET status=?,checked_at_utc=?
                        WHERE plan_id=? AND status='IN_FLIGHT'""",
                        (status,_now(),pid)).rowcount
                    if changed!=1:
                        raise N8NGmailOutputError("n8n_gmail_witness_result_uncertain",503)
                    row=self.db.execute("""SELECT plan_id,execution_id,
                        delivery_id,pinned_send_node_sha256,
                        signed_gmail_id_sha256,status,checked_at_utc
                        FROM n8n_gmail_output_witnesses WHERE plan_id=?""",(pid,)).fetchone()
            except sqlite3.Error:
                raise N8NGmailOutputError("n8n_gmail_witness_result_uncertain",503) from None
        return self._public(row)

    def require_match(self, plan_id, message_id):
        if (type(plan_id) is not str or PLAN_ID.fullmatch(plan_id) is None
                or type(message_id) is not str
                or MESSAGE_ID.fullmatch(message_id) is None):
            raise N8NGmailOutputError("invalid_n8n_gmail_identity",422)
        with self.lock:
            try:
                signed, expected_hash=self._chain(plan_id)
                row=self.db.execute("""SELECT execution_id,delivery_id,
                    pinned_send_node_sha256,signed_gmail_id_sha256,status
                    FROM n8n_gmail_output_witnesses WHERE plan_id=?""",(plan_id,)).fetchone()
            except sqlite3.Error:
                raise N8NGmailOutputError("n8n_gmail_witness_storage_unavailable",503) from None
            if (row is None or row[:4]!=(
                    signed[5],signed[4],_sha(self.node_name),expected_hash)
                    or row[4]!="PROVIDER_SEND_NODE_ID_CORRELATED"
                    or not hmac.compare_digest(expected_hash,_sha(message_id))):
                raise N8NGmailOutputError("n8n_gmail_output_witness_required")
            return True

    def history(self):
        with self.lock:
            try:
                rows=self.db.execute("""SELECT plan_id,execution_id,delivery_id,
                    pinned_send_node_sha256,signed_gmail_id_sha256,status,
                    checked_at_utc FROM n8n_gmail_output_witnesses
                    ORDER BY checked_at_utc DESC,plan_id DESC LIMIT 30""").fetchall()
            except sqlite3.Error:
                raise N8NGmailOutputError("n8n_gmail_witness_storage_unavailable",503) from None
        return {"records":[self._public(x) for x in rows],
                "scope":"OFFICIAL_N8N_PINNED_GMAIL_SEND_NODE_OUTPUT",
                "recipient_delivery_verified":False,
                "causal_business_effect_verified":False}
