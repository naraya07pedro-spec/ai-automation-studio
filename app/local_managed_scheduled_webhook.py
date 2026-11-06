"""Owner-armed automatic dispatch to explicitly selected ENCRYPTED managed webhooks.

Reuses the already hardened ScheduledWebhookAutopilot per immutable connector
identity, each with an isolated durable per-schedule ledger. A connector is
resolved under the manager lock immediately before network I/O so revocation
and credential changes cannot race a send. No model-selected tools or generic
plugin execution. NO plaintext managed-registry autopilot.
"""
import hashlib
from pathlib import Path
import sqlite3
import threading

from local_connector_manager import ManagedConnectorError, _validate_id
from local_outbound_webhook import send_https_once, validate_https_target
from local_scheduled_webhook import ScheduledWebhookAutopilot, AutopilotError

MAX_MANAGED_DISPATCH_PER_TICK = 3


class _ManagedTarget:
    """Never cache a sensitive URL or signing secret across revocation."""

    def __init__(self, manager, connector_id):
        self.manager = manager
        self.connector_id = connector_id

    def _active(self):
        with self.manager._lock:
            try:
                return self.manager._entry(self.connector_id)
            except ManagedConnectorError:
                return None

    @property
    def host(self):
        row = self._active()
        return validate_https_target(row["url"])[0] if row else "revoked"

    @property
    def url(self):
        row = self._active()
        return row["url"] if row else "revoked:" + self.connector_id

    @property
    def secret(self):
        row = self._active()
        return row["secret"] if row else ""

    def sender(self, url, payload, delivery_id, secret):
        # Revocation and outbound HTTP must be serialized. A revoked ID
        # cannot be re-created, and no cached sender retains its credentials.
        with self.manager._lock:
            row = self.manager._entry(self.connector_id)
            if row["url"] != url or row["secret"] != secret:
                raise ManagedConnectorError("connector_credentials_changed", 409)
            sender = self.manager._sender or send_https_once
            return sender(row["url"], payload, delivery_id, row["secret"])


class _ScopedScheduledWebhook(ScheduledWebhookAutopilot):
    """Stable delivery ID is unique per connector + schedule + due slot."""

    def __init__(self, connector_id, *args, **kwargs):
        self.connector_id = connector_id
        super().__init__(*args, dispatch_action="MANAGED_AUTOPILOT_DISPATCH",
                         max_dispatch_per_tick=1, **kwargs)

    def _payload(self, schedule_id, receipt, workflow_id):
        payload = super()._payload(schedule_id, receipt, workflow_id)
        seed = ("aas.managed.scheduled.v1:" + self.connector_id + ":" +
                schedule_id + ":" + receipt["scheduled_for_utc"])
        payload["delivery_id"] = hashlib.sha256(seed.encode("ascii")).hexdigest()[:32]
        payload["connector_id"] = self.connector_id
        return payload


class ManagedScheduledWebhookAutopilot:
    def __init__(self, scheduler, manager, audit, directory, clock=None):
        if manager is None or manager._vault is None:
            raise AutopilotError("encrypted_managed_connections_required", 403)
        self.scheduler = scheduler
        self.manager = manager
        self.audit = audit
        self.root = Path(directory).expanduser()
        if self.root.is_symlink() or (self.root.exists() and not self.root.is_dir()):
            raise AutopilotError("managed_autopilot_unsafe_storage_path", 503)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink():
            raise AutopilotError("managed_autopilot_unsafe_storage_path", 503)
        self.clock = clock
        self.lock = threading.RLock()
        self._policies = {}
        self._scan_cursor = 0
        # One POST at most per connector per scan, three across all connectors.
        self.manager.scheduled_enabled = True

    def _path(self, connector_id):
        _validate_id(connector_id)
        return self.root / (connector_id + ".sqlite3")

    def _get(self, connector_id, *, require_active, create=False):
        _validate_id(connector_id)
        with self.lock:
            # Never accept IDs outside the already configured owner registry.
            with self.manager._lock:
                self.manager._entry(connector_id, require_active=require_active)
            existing = self._policies.get(connector_id)
            if existing is not None:
                return existing
            path = self._path(connector_id)
            if not create and not path.exists():
                raise AutopilotError("managed_schedule_policy_not_found", 404)
            target = _ManagedTarget(self.manager, connector_id)
            extra = {"clock": self.clock} if self.clock is not None else {}
            policy = _ScopedScheduledWebhook(
                connector_id, self.scheduler, target, self.audit, path, **extra)
            self._policies[connector_id] = policy
            return policy

    @staticmethod
    def _without_connector(request, keys):
        if type(request) is not dict or set(request) != keys:
            raise AutopilotError("invalid_managed_autopilot_fields")
        try:
            _validate_id(request["connector_id"])
        except ManagedConnectorError as exc:
            raise AutopilotError(str(exc), exc.status) from None
        return {k: v for k, v in request.items() if k != "connector_id"}

    def preview(self, request):
        values = self._without_connector(
            request, {"connector_id","schedule_id","max_sends","expires_days"})
        cid = request["connector_id"]
        try:
            return {"connector_id": cid,
                    **self._get(cid, require_active=True, create=True).preview(values)}
        except ManagedConnectorError as exc:
            raise AutopilotError(str(exc),exc.status) from None

    def arm(self, request):
        values = self._without_connector(
            request, {"connector_id","schedule_id","max_sends",
                      "expires_days","preview_token"})
        cid = request["connector_id"]
        try:
            return {"connector_id": cid,
                    **self._get(cid, require_active=True, create=False).arm(values)}
        except ManagedConnectorError as exc:
            raise AutopilotError(str(exc),exc.status) from None

    def stop(self, request):
        values = self._without_connector(
            request, {"connector_id","schedule_id","expected_epoch"})
        cid = request["connector_id"]
        try:
            return {"connector_id": cid,
                    **self._get(cid, require_active=False, create=False).stop(
                        values["schedule_id"],values["expected_epoch"])}
        except ManagedConnectorError as exc:
            raise AutopilotError(str(exc),exc.status) from None

    def list(self):
        policies, attempts = [], []
        # Return only redacted records for existing owner-defined connector IDs.
        for connection in self.manager.list()["connectors"]:
            cid = connection["connector_id"]
            if not self._path(cid).is_file():
                continue
            db = self._get(cid, require_active=False, create=False)
            snapshot = db.list()
            policies.extend({"connector_id":cid,**p} for p in snapshot["policies"])
            attempts.extend({"connector_id":cid,**a} for a in snapshot["attempts"])
        return {"enabled": True, "policies":policies,"attempts":attempts[:100],
                "scope":"encrypted_owner_managed_https_only",
                "requires_owner_approval":True,"model_tool_access":False}

    def dispatch_due(self):
        count = 0
        # Fail closed and keep the recurring worker alive if the manager registry
        # becomes unavailable. A broken connector must never stop local rules.
        try:
            connections = self.manager.list()["connectors"]
        except (ManagedConnectorError, OSError, sqlite3.Error, ValueError):
            return 0
        if not connections:
            return 0
        # Round-robin preserves fairness when four active targets compete for
        # the three-send global tick budget. Do not block the local scheduler
        # on an unbounded number of sequential HTTPS timeouts.
        with self.lock:
            cursor = self._scan_cursor % len(connections)
            self._scan_cursor = (cursor + 1) % len(connections)
        ordered = connections[cursor:] + connections[:cursor]
        # Only pre-existing policy files are scanned, never arbitrary paths.
        for connection in ordered:
            if count >= MAX_MANAGED_DISPATCH_PER_TICK:
                break
            cid = connection["connector_id"]
            if not self._path(cid).is_file():
                continue
            try:
                count += self._get(cid, require_active=False).dispatch_due()
            except (AutopilotError, ManagedConnectorError, OSError, sqlite3.Error):
                # A failure in one connection must never block the others.
                pass
        return count

    def close(self):
        with self.lock:
            for policy in self._policies.values():
                policy.close()
            self._policies.clear()
            self.manager.scheduled_enabled = False
