"""Owner-managed multi-endpoint HTTPS webhook connections, disabled by default.

This is a restrictive owner-only connector registry, NOT an encrypted secrets
vault, MCP marketplace or an arbitrary agent HTTP client. Credential material
is written to a private OS-permissioned JSON file (mode 0600 POSIX, directory
0700). Windows inherited ACLs must be verified by the owner. No secrets or
webhook paths are returned through HTTP endpoints, logs or exception text.
All external POSTs use the existing immutable local-workflow receipt and
one-use, no-retry outbound delivery ledger per connector.

New connections have no scheduler/agent automatic dispatch capability.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import threading

from local_outbound_webhook import LocalOutboundWebhook, OutboundError, validate_https_target
from local_source_policy import secure_replace_json, SourcePolicyError
from local_connector_vault import EncryptedConnectorVault, VaultError

ID = re.compile(r"^[a-z][a-z0-9_-]{2,31}$", re.ASCII)
MAX_ACTIVE = 4
MAX_TOTAL = 12
MAX_URL = 420
MAX_SECRET = 128


class ManagedConnectorError(Exception):
    def __init__(self, reason, status=422):
        super().__init__(reason)
        self.status = status


def _validate_id(value):
    if type(value) is not str or not ID.fullmatch(value):
        raise ManagedConnectorError("invalid_connector_id")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LocalConnectorManager:
    def __init__(self, core, filename, sender=None, clock=None, vault_key=None):
        self.core = core
        self.path = Path(filename).expanduser()
        self._lock = threading.RLock()
        self._clients = {}
        self._sender = sender
        self._clock = clock
        self._failed = False
        self._vault = (EncryptedConnectorVault(self.path,vault_key)
                       if vault_key is not None else None)
        if self._vault is None:
            self._init_store()
        self._entries = self._read()

    def _init_store(self):
        p = self.path
        if p.is_symlink() or (p.exists() and not p.is_file()):
            raise ManagedConnectorError("connector_registry_unsafe_path", 503)
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if p.parent.is_symlink():
            raise ManagedConnectorError("connector_registry_unsafe_directory", 503)
        if os.name == "posix":
            os.chmod(p.parent, 0o700)
            if p.exists() and stat.S_IMODE(p.stat().st_mode) & 0o077:
                raise ManagedConnectorError("connector_registry_insecure_permissions", 503)
        if not p.exists():
            try:
                secure_replace_json(p, {"version":1, "entries":[]})
            except (SourcePolicyError, OSError):
                raise ManagedConnectorError("connector_registry_unavailable", 503) from None

    def _read(self):
        try:
            if self._vault is not None:
                data = self._vault.read()
            else:
                if self.path.stat().st_size > 4096:
                    raise ValueError("too large")
                data = json.loads(self.path.read_text(encoding="utf-8"))
            if (type(data) is not dict or set(data) != {"version","entries"}
                    or data["version"] != 1 or type(data["entries"]) is not list
                    or len(data["entries"]) > MAX_TOTAL):
                raise ValueError("bad structure")
            ids = set()
            for e in data["entries"]:
                if (type(e) is not dict or
                        set(e) != {"connector_id","label","url","secret","status","created_utc"}):
                    raise ValueError("bad entry")
                _validate_id(e["connector_id"])
                if e["connector_id"] in ids:
                    raise ValueError("duplicate")
                ids.add(e["connector_id"])
                if e["status"] not in ("ACTIVE","REVOKED"):
                    raise ValueError("bad state")
                if type(e["label"]) is not str or not 1 <= len(e["label"]) <= 60:
                    raise ValueError("bad label")
                if type(e["created_utc"]) is not str:
                    raise ValueError("bad time")
                if type(e["url"]) is not str or type(e["secret"]) is not str:
                    raise ValueError("bad secret")
                if e["status"] == "ACTIVE":
                    if not 12 <= len(e["url"]) <= MAX_URL or len(e["secret"]) > MAX_SECRET:
                        raise ValueError("oversized credential")
                    validate_https_target(e["url"])
                elif e["url"] or e["secret"]:
                    raise ValueError("revoked credential retained")
            return data["entries"]
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, OutboundError,
                ManagedConnectorError, VaultError):
            raise ManagedConnectorError("connector_registry_corrupt_or_locked", 503) from None

    def _persist(self, entries):
        if self._failed:
            raise ManagedConnectorError("connector_registry_requires_restart", 503)
        try:
            if self._vault is not None:
                self._vault.write({"version":1,"entries":entries})
            else:
                secure_replace_json(self.path, {"version":1,"entries":entries})
        except (SourcePolicyError, OSError, VaultError):
            # Writes may have reached the filesystem before the reported
            # durability error. Refuse to operate until a fresh restart.
            self._failed = True
            raise ManagedConnectorError("connector_registry_requires_restart", 503) from None
        self._entries = entries

    @staticmethod
    def _public(row):
        host = validate_https_target(row["url"])[0] if row["status"] == "ACTIVE" else None
        return {"connector_id":row["connector_id"], "label":row["label"],
                "kind":"https_webhook", "status":row["status"],
                "destination_hostname":host, "created_utc":row["created_utc"],
                "supports_manual_send":row["status"] == "ACTIVE",
                "supports_scheduled_send":False,
                "secret_exportable":False}

    def list(self):
        with self._lock:
            if self._failed:
                raise ManagedConnectorError("connector_registry_requires_restart",503)
            return {"connectors":[self._public(x) for x in self._entries],
                    "max_active":MAX_ACTIVE, "max_total":MAX_TOTAL,
                    "storage":("AES_256_GCM_EXTERNAL_MASTER_KEY" if self._vault is not None
                               else "OS_PERMISSION_PROTECTED_NOT_ENCRYPTED"),
                    "requires_fresh_owner_password_for_changes":True,
                    "model_tool_access":False, "scheduled_dispatch_enabled":False}

    def _entry(self, connector_id, *, require_active=True):
        _validate_id(connector_id)
        if self._failed:
            raise ManagedConnectorError("connector_registry_requires_restart",503)
        row = next((r for r in self._entries if r["connector_id"] == connector_id),None)
        if row is None:
            raise ManagedConnectorError("connector_not_found",404)
        if require_active and row["status"] != "ACTIVE":
            raise ManagedConnectorError("connector_revoked",409)
        return row

    def _ledger_path(self, connector_id):
        # Reuse immutable per-ID send receipts between legacy and encrypted
        # storage modes: a vault migration must NOT reset no-retry history.
        return self.path.parent / "managed-webhook-ledgers" / (connector_id+".sqlite3")

    def _client(self, row):
        key = row["connector_id"]
        existing = self._clients.get(key)
        if existing is not None:
            return existing
        client = LocalOutboundWebhook(
            self.core, self._ledger_path(key),
            url=row["url"] if row["status"] == "ACTIVE" else None,
            secret=row["secret"] if row["status"] == "ACTIVE" else "",
            sender=self._sender, **({"clock":self._clock} if self._clock else {}))
        self._clients[key] = client
        return client

    def add(self, data):
        if type(data) is not dict or set(data) != {"connector_id","label","url","signing_secret"}:
            raise ManagedConnectorError("invalid_connector_fields")
        key=_validate_id(data["connector_id"])
        label,url,secret=data["label"],data["url"],data["signing_secret"]
        if (type(label) is not str or not 1 <= len(label.strip()) <= 60
                or any(ord(c)<32 for c in label)
                or type(url) is not str or not 12 <= len(url) <= MAX_URL
                or type(secret) is not str or len(secret)>MAX_SECRET
                or any(ord(c)<33 or ord(c)>126 for c in secret)):
            raise ManagedConnectorError("invalid_connector_configuration")
        try:
            validate_https_target(url)
        except OutboundError:
            raise ManagedConnectorError("invalid_webhook_https_url") from None
        with self._lock:
            if any(e["connector_id"]==key for e in self._entries):
                raise ManagedConnectorError("connector_id_permanently_reserved",409)
            if len(self._entries)>=MAX_TOTAL:
                raise ManagedConnectorError("connector_history_capacity_reached",409)
            if sum(e["status"]=="ACTIVE" for e in self._entries)>=MAX_ACTIVE:
                raise ManagedConnectorError("active_connector_capacity_reached",409)
            entry={"connector_id":key, "label":label.strip(), "url":url,
                   "secret":secret, "status":"ACTIVE", "created_utc":_now()}
            # No network action, no ledger mutation, no model permission.
            self._persist([*self._entries,entry])
            return self._public(entry)

    def revoke(self, connector_id):
        with self._lock:
            row=self._entry(connector_id)
            updated=[]
            for e in self._entries:
                if e["connector_id"]==connector_id:
                    replacement={**e,"status":"REVOKED","url":"","secret":""}
                    updated.append(replacement)
                else:
                    updated.append(e)
            self._persist(updated)
            client=self._clients.pop(connector_id,None)
            if client is not None:
                client.close()
            return self._public(replacement)

    def preview(self, data):
        if type(data) is not dict or set(data) != {"connector_id","workflow_id","event_id"}:
            raise ManagedConnectorError("invalid_managed_preview_fields")
        with self._lock:
            row=self._entry(data["connector_id"])
            result=self._client(row).preview(
                {"workflow_id":data["workflow_id"],"event_id":data["event_id"]})
            return {**result,"connector_id":row["connector_id"],
                    "connector_label":row["label"]}

    def commit(self, data):
        if (type(data) is not dict or
                set(data) != {"connector_id","workflow_id","event_id",
                              "preview_token","payload_sha256"}):
            raise ManagedConnectorError("invalid_managed_commit_fields")
        with self._lock:
            row=self._entry(data["connector_id"])
            result=self._client(row).commit({k:v for k,v in data.items()
                                              if k!="connector_id"})
            return {**result,"connector_id":row["connector_id"]}

    def history(self, connector_id):
        with self._lock:
            row=self._entry(connector_id,require_active=False)
            return {**self._client(row).history(), "connector_id":row["connector_id"],
                    "connector_status":row["status"]}

    def close(self):
        with self._lock:
            for client in self._clients.values():
                client.close()
            self._clients.clear()
