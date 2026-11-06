"""Two-phase, owner-only local JSON aggregate export backed by a fixed tool registry.

The reviewed plan does NOT call this write action. A separate authenticated
preview and a fresh owner confirmation are mandatory. The preview ticket is
short-lived and process-local; the resulting file is durable and create-only.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from local_csv_profile import profile_selected_csv, CSVProfileError
from local_plan_artifacts import LocalPlanArtifactExporter, PlanArtifactError
from local_tool_registry import ACTION_TOOL, ToolPermissionError, require_owner_action
from local_security_audit import SecurityAuditError

PLAN_ID = re.compile(r"[0-9a-f]{24}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_OUTPUT_BYTES = 32768
TICKET_TTL_SECONDS = 300
MAX_TICKETS = 8
AUDIT_ACTION = "LOCAL_CSV_PROFILE_EXPORT"


class ToolActionError(ValueError):
    pass


class LocalCSVProfileActionGate:
    def __init__(self, plan_store, directory, audit, *, enabled=True):
        self.store = plan_store
        self.writer = LocalPlanArtifactExporter(directory)
        self.audit = audit
        self.enabled = enabled
        self.lock = threading.RLock()
        self._tickets = {}

    def _require(self, action):
        try:
            require_owner_action(action, enabled=self.enabled)
        except ToolPermissionError:
            raise ToolActionError("local_tool_permission_denied") from None
        if self.store.csv_profiler is None:
            raise ToolActionError("local_csv_permission_revoked")

    def _material(self, plan_id):
        if type(plan_id) is not str or PLAN_ID.fullmatch(plan_id) is None:
            raise ToolActionError("invalid_action_plan_id")
        # Only the store can access the consented source. Never accept source
        # bytes, filename, path, selected columns or result JSON through HTTP.
        with self.store.lock:
            saved = self.store.get(plan_id)
            if saved["status"] != "COMPLETED":
                raise ToolActionError("action_requires_completed_plan")
            if saved["batch_run_status"] in ("RUNNING", "STOPPING"):
                raise ToolActionError("action_batch_active")
            if saved["csv_source"] is None or self.store.csv_profiler is None:
                raise ToolActionError("action_csv_source_unavailable")
            csv = self.store._stored_csv(plan_id)
            profiles = []
            for index, (step, result) in enumerate(
                    zip(saved["steps"], saved["results"]), 1):
                if step["tool"] != "local_csv_profile":
                    continue
                if (type(result) is not dict
                        or result.get("tool") != "local_csv_profile"
                        or type(result.get("output")) is not str):
                    raise ToolActionError("action_profile_invalid")
                try:
                    expected = profile_selected_csv(csv, step["args"]["columns"])
                    observed = json.loads(result["output"])
                except (CSVProfileError, TypeError, ValueError, KeyError):
                    raise ToolActionError("action_profile_invalid") from None
                if type(observed) is not dict or observed != expected:
                    raise ToolActionError("action_profile_invalid")
                profiles.append({
                    "step_index": index,
                    "source_output_sha256": sha256(
                        result["output"].encode("utf-8")).hexdigest(),
                    "profile": expected,
                })
            if not profiles:
                raise ToolActionError("action_requires_csv_profile")
            bundle = {
                "format": "LOCAL_AGENT_CSV_PROFILE_EXPORT_V1",
                "plan_id": plan_id,
                "source_csv_sha256": csv["sha256"],
                "source_filename": csv["filename"],
                "source_scope": "OWNER_CONSENTED_LOCAL_PLAINTEXT_SNAPSHOT",
                "profiles": profiles,
                "interpretation_verified": False,
                "raw_records_included": False,
                "model_calls": 0,
                "external_write_count": 0,
            }
            raw = (json.dumps(bundle, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) + "\n").encode("utf-8")
        if not 0 < len(raw) <= MAX_OUTPUT_BYTES:
            raise ToolActionError("action_output_too_large")
        return raw, bundle

    def preview(self, action, plan_id):
        self._require(action)
        raw, bundle = self._material(plan_id)
        ticket = secrets.token_urlsafe(24)
        digest = sha256(raw).hexdigest()
        with self.lock:
            now = time.monotonic()
            self._tickets = {k: v for k, v in self._tickets.items()
                             if v["expires"] > now}
            if len(self._tickets) >= MAX_TICKETS:
                raise ToolActionError("action_preview_capacity")
            self._tickets[ticket] = {
                "plan_id": plan_id, "sha256": digest,
                "expires": now + TICKET_TTL_SECONDS,
            }
        return {
            "action": ACTION_TOOL,
            "plan_id": plan_id,
            "preview_token": ticket,
            "preview_sha256": digest,
            "expires_in_seconds": TICKET_TTL_SECONDS,
            "relative_workspace": ".ai-automation-studio/exports",
            "filename": "local-agent-csv-profile-" + plan_id + ".json",
            "bytes": len(raw),
            "risk": "ONE_LOCAL_CREATE_ONLY_PLAINTEXT_FILE",
            "side_effects": {
                "local_file_write_count_if_new": 1,
                "existing_file_overwrite_allowed": False,
                "external_write_count": 0,
                "new_model_calls": 0,
            },
            "preview": bundle,
        }

    def commit(self, action, plan_id, preview_token, preview_sha256):
        self._require(action)
        if (type(preview_token) is not str
                or len(preview_token) != 32
                or type(preview_sha256) is not str
                or DIGEST.fullmatch(preview_sha256) is None):
            raise ToolActionError("invalid_action_commit")
        with self.lock:
            # A ticket cannot authorize a second write or be reused after
            # a lost HTTP response. An owner can explicitly preview again.
            grant = self._tickets.pop(preview_token, None)
            if (grant is None or grant["expires"] <= time.monotonic()
                    or grant["plan_id"] != plan_id
                    or grant["sha256"] != preview_sha256):
                raise ToolActionError("action_preview_expired_or_changed")
            if not self.audit.ready():
                raise ToolActionError("action_audit_unavailable")
            raw, _ = self._material(plan_id)
            if sha256(raw).hexdigest() != preview_sha256:
                raise ToolActionError("action_preview_expired_or_changed")
            try:
                self.audit.record(AUDIT_ACTION, "ATTEMPT")
            except SecurityAuditError:
                raise ToolActionError("action_audit_unavailable") from None
            try:
                receipt = self._create_only(plan_id, raw)
            except (OSError, PlanArtifactError, ToolActionError):
                try:
                    self.audit.record(AUDIT_ACTION, "FAILED")
                except SecurityAuditError:
                    raise ToolActionError("action_write_outcome_uncertain") from None
                raise
            try:
                self.audit.record(AUDIT_ACTION, "SUCCESS")
            except SecurityAuditError:
                raise ToolActionError("action_write_outcome_uncertain") from None
            return receipt

    def _create_only(self, plan_id, raw):
        name = "local-agent-csv-profile-" + plan_id + ".json"
        # Reuse exactly the existing fixed workspace and symlink/permission
        # checks. No browser-supplied path. Never replace an existing file.
        with self.writer.lock:
            self.writer._workspace()
            target = self.writer.root / name
            temp = self.writer.root / (".write-" + secrets.token_hex(16))
            file_fd = None
            created = False
            try:
                flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | getattr(os, "O_NOFOLLOW", 0))
                file_fd = os.open(str(temp), flags, 0o600)
                with os.fdopen(file_fd, "wb") as stream:
                    file_fd = None
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(str(temp), str(target), follow_symlinks=False)
                    created = True
                except FileExistsError:
                    self.writer._matching_existing(target, raw)
            except PlanArtifactError:
                raise ToolActionError("action_target_conflict") from None
            except (OSError, ValueError):
                raise ToolActionError("action_workspace_unavailable") from None
            finally:
                if file_fd is not None:
                    os.close(file_fd)
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
            return {
                "action": ACTION_TOOL,
                "status": "CREATED" if created else "EXISTS_IDENTICAL",
                "filename": name,
                "relative_workspace": ".ai-automation-studio/exports",
                "bytes": len(raw),
                "sha256": sha256(raw).hexdigest(),
                "local_file_write_count": 1 if created else 0,
                "external_write_count": 0,
                "model_calls": 0,
            }
