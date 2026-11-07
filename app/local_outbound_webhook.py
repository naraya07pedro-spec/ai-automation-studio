"""One REAL opt-in outbound HTTPS webhook connector (n8n-compatible).

Sends an immutable local SQLite workflow receipt, never an arbitrary model
payload. Owner must inspect an exact preview and re-enter the current password.

No automatic retry of HTTP side effects. A remote 2xx only acknowledges a
webhook endpoint, NOT completion of a downstream n8n workflow.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
from local_private_sqlite_path import prepare_private_sqlite, PrivateSQLitePathError
import re
import secrets
import socket
import sqlite3
import ssl
import threading
from urllib.parse import urlsplit

from local_workflow_core import ID_PATTERN, EVENT_PATTERN

MAX_URL = 1800
MAX_ATTEMPTS = 1000
PREVIEW_TTL = 300
_HOSTNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,250}[A-Za-z0-9])?$", re.ASCII)


class OutboundError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def _utc():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_https_target(value):
    """No IP URLs, local domain, userinfo, custom ports, or invalid URLs."""
    if (type(value) is not str or not 12 <= len(value) <= MAX_URL
            or any(ord(c) < 33 or ord(c) > 126 for c in value)
            or "\\" in value or "#" in value):
        raise OutboundError("invalid_webhook_https_url")
    try:
        uri = urlsplit(value)
        hostname = uri.hostname
        port = uri.port
    except ValueError:
        raise OutboundError("invalid_webhook_https_url") from None
    if (uri.scheme != "https" or uri.username is not None
            or uri.password is not None or not hostname
            or not _HOSTNAME.fullmatch(hostname)
            or hostname.lower() in ("localhost", "localhost.localdomain")
            or hostname.lower().endswith((".local", ".internal", ".localhost", ".test",
                                          ".invalid", ".example"))
            or "." not in hostname
            or hostname.startswith("-") or hostname.endswith("-")
            or ".." in hostname or ".-" in hostname or "-." in hostname
            or port not in (None, 443)
            or uri.path in ("", "/") or len(uri.path) > 1024):
        raise OutboundError("invalid_webhook_https_url")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise OutboundError("webhook_ip_literal_disallowed")
    return hostname.lower(), (uri.path or "/") + ("?" + uri.query if uri.query else "")


def _resolve_public_ipv4(host):
    """Resolve ONCE; fail closed unless every resolved address is globally routable."""
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise OutboundError("webhook_dns_unavailable", 503) from None
    addresses = []
    for family, _, _, _, sockaddr in records:
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise OutboundError("webhook_dns_unsupported", 503)
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise OutboundError("webhook_dns_untrusted", 503) from None
        if not address.is_global:
            raise OutboundError("webhook_dns_nonpublic_blocked", 403)
        if family == socket.AF_INET:
            addresses.append(str(address))
    if not addresses:
        raise OutboundError("webhook_ipv4_required", 503)
    return addresses[0]


class _PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, hostname, pinned_ip):
        super().__init__(hostname, port=443, timeout=8, context=ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def connect(self):
        raw = socket.create_connection((self._pinned_ip, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def send_https_once(url, payload, delivery_id, secret):
    """One HTTPS POST to a pinned public IP, real hostname verification, no redirects."""
    host, path = validate_https_target(url)
    ip = _resolve_public_ipv4(host)
    body = _canonical(payload)
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": "AI-Automation-Studio/1.0",
        "X-AAS-Delivery-ID": delivery_id,
    }
    if secret:
        headers["X-AAS-Signature-SHA256"] = hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    conn = _PinnedTLS(host, ip)
    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        status = response.status
        # Do not consume or reveal response data; provider content is untrusted.
        return status
    finally:
        conn.close()


class LocalOutboundWebhook:
    def __init__(self, core, database=":memory:", url=None, secret="", sender=None, clock=_utc):
        self.core = core
        self.url = url
        self.secret = secret
        self.sender = sender or send_https_once
        self.clock = clock
        self._lock = threading.RLock()
        self._busy = threading.Lock()
        self.host = None
        if url:
            self.host, _ = validate_https_target(url)
        if type(secret) is not str or len(secret) > 512:
            raise OutboundError("invalid_signing_secret")
        try:
            database = prepare_private_sqlite(database)
        except PrivateSQLitePathError:
            raise OutboundError("webhook_private_storage_unsafe",503) from None
        self.db = sqlite3.connect(str(database), check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA synchronous=FULL")
        if str(database) != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS webhook_deliveries (
                workflow_id TEXT NOT NULL, event_id TEXT NOT NULL,
                status TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                target_sha256 TEXT NOT NULL, token_sha256 TEXT NOT NULL,
                expires_utc TEXT NOT NULL, delivery_id TEXT NOT NULL,
                http_status INTEGER, updated_utc TEXT NOT NULL,
                PRIMARY KEY(workflow_id,event_id))""")

    def catalog(self):
        return {
            "connectors": [
                {"id": "outbound_https_webhook", "label": "n8n / custom HTTPS webhook",
                 "configured": self.host is not None,
                 "capabilities": ["preview_saved_local_workflow_receipt",
                                  "owner_approved_single_https_post"],
                 "permission": "OWNER_PASSWORD_EACH_DELIVERY",
                 "external_write_enabled": self.host is not None},
                {"id": "github_public_issues", "label": "GitHub public issues",
                 "configured": None, "capabilities": ["public_read_only"],
                 "permission": "SEPARATE_SOURCE_GRANT"},
                {"id": "hacker_news_public", "label": "Hacker News public items",
                 "configured": None, "capabilities": ["public_read_only"],
                 "permission": "SEPARATE_SOURCE_GRANT"},
                {"id": "greenhouse_public", "label": "Greenhouse public job evidence",
                 "configured": None, "capabilities": ["owner_click_public_read_only"],
                 "permission": "SEPARATE_ENABLE_FLAG"},
            ],
            "webhook_destination_hostname": self.host,
            "webhook_signatures_configured": bool(self.secret),
            "external_write_default": "DISABLED_UNTIL_PREVIEW_AND_OWNER_CONFIRMATION",
            "external_write_count": 0,
        }

    def _run(self, workflow_id, event_id):
        if (type(workflow_id) is not str or not ID_PATTERN.fullmatch(workflow_id)
                or type(event_id) is not str or not EVENT_PATTERN.fullmatch(event_id)):
            raise OutboundError("invalid_workflow_event_identity")
        status, run = self.core.get_run(workflow_id, event_id)
        if status != 200:
            raise OutboundError(run["error"], status)
        return run

    @staticmethod
    def _payload(run, delivery_id):
        return {
            "schema": "aas.local_workflow_receipt.v1",
            "delivery_id": delivery_id,
            "workflow_id": run["workflow_id"],
            "event_id": run["event_id"],
            "workflow_version": run["workflow_version"],
            "decision": run["decision"],
            "reason": run["reason"],
            "created_at": run["created_at"],
        }

    def preview(self, request):
        if not self.host:
            raise OutboundError("webhook_not_configured", 403)
        if type(request) is not dict or set(request) != {"workflow_id", "event_id"}:
            raise OutboundError("invalid_preview_fields")
        run = self._run(request["workflow_id"], request["event_id"])
        delivery_id = _sha(_canonical({"workflow_id": run["workflow_id"],
                                       "event_id": run["event_id"]}))[:32]
        payload = self._payload(run, delivery_id)
        digest = _sha(_canonical(payload))
        target_digest = _sha(self.url.encode("utf-8"))
        token = secrets.token_urlsafe(32)
        expires = _iso(self.clock() + timedelta(seconds=PREVIEW_TTL))
        now = _iso(self.clock())
        with self._lock, self.db:
            prior = self.db.execute("""SELECT status FROM webhook_deliveries
                WHERE workflow_id=? AND event_id=?""",
                (run["workflow_id"], run["event_id"])).fetchone()
            if prior is not None and prior[0] != "PREVIEWED":
                raise OutboundError("webhook_delivery_already_claimed", 409)
            if prior is None:
                count = self.db.execute("SELECT count(*) FROM webhook_deliveries").fetchone()[0]
                if count >= MAX_ATTEMPTS:
                    raise OutboundError("webhook_delivery_capacity_reached", 409)
                self.db.execute("""INSERT INTO webhook_deliveries
                    (workflow_id,event_id,status,payload_sha256,target_sha256,
                    token_sha256,expires_utc,delivery_id,updated_utc)
                    VALUES(?,?,'PREVIEWED',?,?,?,?,?,?)""",
                    (run["workflow_id"], run["event_id"],digest,target_digest,
                     _sha(token.encode("ascii")),expires,delivery_id,now))
            else:
                self.db.execute("""UPDATE webhook_deliveries
                    SET payload_sha256=?,target_sha256=?,token_sha256=?,
                    expires_utc=?,delivery_id=?,updated_utc=?
                    WHERE workflow_id=? AND event_id=? AND status='PREVIEWED'""",
                    (digest,target_digest,_sha(token.encode("ascii")),expires,
                     delivery_id,now,run["workflow_id"],run["event_id"]))
        return {"status": "PREVIEW_ONLY", "workflow_id": run["workflow_id"],
                "event_id": run["event_id"],
                "destination_hostname": self.host,
                "payload": payload, "payload_sha256": digest,
                "preview_token": token, "expires_in_seconds": PREVIEW_TTL,
                "external_write_count": 0,
                "warning": "2xx only confirms webhook acceptance, not downstream completion"}

    def commit(self, request):
        if not self.host:
            raise OutboundError("webhook_not_configured", 403)
        if (type(request) is not dict
                or set(request) != {"workflow_id", "event_id",
                                    "preview_token", "payload_sha256"}
                or type(request["preview_token"]) is not str
                or len(request["preview_token"]) != 43
                or type(request["payload_sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", request["payload_sha256"])):
            raise OutboundError("invalid_commit_fields")
        if not self._busy.acquire(blocking=False):
            raise OutboundError("webhook_dispatch_busy", 429)
        try:
            run = self._run(request["workflow_id"], request["event_id"])
            delivery_id = _sha(_canonical({"workflow_id": run["workflow_id"],
                                           "event_id": run["event_id"]}))[:32]
            payload = self._payload(run, delivery_id)
            digest = _sha(_canonical(payload))
            token_hash = _sha(request["preview_token"].encode("ascii"))
            with self._lock, self.db:
                row = self.db.execute("""SELECT status,payload_sha256,target_sha256,
                    token_sha256,expires_utc,delivery_id FROM webhook_deliveries
                    WHERE workflow_id=? AND event_id=?""",
                    (run["workflow_id"], run["event_id"])).fetchone()
                if row is None or row[0] != "PREVIEWED":
                    raise OutboundError("webhook_preview_unavailable_or_used", 409)
                if (_iso(self.clock()) >= row[4] or
                        not hmac.compare_digest(row[1], digest) or
                        not hmac.compare_digest(row[1], request["payload_sha256"]) or
                        not hmac.compare_digest(row[2], _sha(self.url.encode("utf-8"))) or
                        not hmac.compare_digest(row[3], token_hash) or
                        row[5] != delivery_id):
                    raise OutboundError("webhook_preview_expired_or_changed", 409)
                self.db.execute("""UPDATE webhook_deliveries
                    SET status='IN_FLIGHT',updated_utc=?
                    WHERE workflow_id=? AND event_id=? AND status='PREVIEWED'
                    AND token_sha256=? AND payload_sha256=? AND target_sha256=?""",
                    (_iso(self.clock()), run["workflow_id"], run["event_id"],
                     token_hash, digest, _sha(self.url.encode("utf-8"))))
                # Cross-process compare-and-swap: another instance cannot
                # also claim this write even if it observed the earlier preview.
                if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                    raise OutboundError("webhook_delivery_already_claimed", 409)
            # Durable IN_FLIGHT claim precedes all external network activity.
            # Crash or timeout may have sent the POST: NEVER blind retry.
            try:
                http_status = self.sender(self.url, payload, delivery_id, self.secret)
                accepted = type(http_status) is int and 200 <= http_status < 300
            except Exception:
                http_status, accepted = None, False
            state = "REMOTE_ACCEPTED" if accepted else "OUTCOME_UNCERTAIN"
            try:
                with self._lock, self.db:
                    self.db.execute("""UPDATE webhook_deliveries
                        SET status=?,http_status=?,updated_utc=?
                        WHERE workflow_id=? AND event_id=? AND status='IN_FLIGHT'""",
                        (state,http_status,_iso(self.clock()),
                         run["workflow_id"], run["event_id"]))
            except sqlite3.Error:
                raise OutboundError("webhook_delivery_state_unavailable_check_ledger", 503) from None
            return {"status": state, "workflow_id": run["workflow_id"],
                    "event_id": run["event_id"], "delivery_id": delivery_id,
                    "http_status": http_status,
                    "external_write_attempts": 1,
                    "downstream_success_verified": False,
                    "no_automatic_retry": True}
        finally:
            self._busy.release()

    def history(self):
        with self._lock:
            rows = self.db.execute("""SELECT workflow_id,event_id,status,delivery_id,
                http_status,updated_utc FROM webhook_deliveries
                ORDER BY updated_utc DESC LIMIT 25""").fetchall()
        return {"deliveries": [
            {"workflow_id": wid, "event_id": eid, "status": status,
             "delivery_id": did, "http_status": code, "updated_utc": updated}
            for wid,eid,status,did,code,updated in rows],
            "max_deliveries": MAX_ATTEMPTS,
            "external_write_count": 0}

    def close(self):
        with self._lock:
            self.db.close()
