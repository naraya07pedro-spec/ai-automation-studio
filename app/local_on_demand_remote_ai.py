"""One-shot owner-selected paid cloud text inference through the private workbench.

Explicit per-request paid consent. Durable request-idempotency ledger and the
same model-safety gate used by scheduled AI. No model tools, retries, external
business writes, auto-resume, or prompt persistence.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading

from local_model_safety import ModelSafetyError
from multi_provider_readonly import ANTHROPIC_MODEL, GEMINI_MODEL
from local_ollama_agent import MAX_PROMPT
from openai_readonly_responses import valid_remote_model_name, RemoteInferenceError, MAX_OUTPUT_TOKENS

REQUEST_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
MAX_ROWS = 200
MAX_DAILY_ATTEMPTS = 5
MAX_ANSWER = 6000
MAX_MODEL_ALLOWLIST = 24
PROVIDERS = frozenset(("openai_responses","anthropic_messages","gemini_generate_content"))


class OnDemandRemoteAIError(RuntimeError):
    def __init__(self, reason, status=422):
        super().__init__(reason)
        self.status = status


def validate_model_allowlist(items):
    """Owner exact provider/model pairs. None retains legacy choice mode."""
    if items is None:
        return None
    if (type(items) is not list or not 1 <= len(items) <= MAX_MODEL_ALLOWLIST):
        raise OnDemandRemoteAIError("invalid_remote_model_allowlist", 422)
    choices = set()
    for item in items:
        if type(item) is not str or ":" not in item or len(item) > 160:
            raise OnDemandRemoteAIError("invalid_remote_model_allowlist", 422)
        provider, model = item.split(":", 1)
        if (provider not in PROVIDERS or not valid_remote_model_name(model)
                or (provider == "anthropic_messages"
                    and ANTHROPIC_MODEL.fullmatch(model) is None)
                or (provider == "gemini_generate_content"
                    and GEMINI_MODEL.fullmatch(model) is None)
                or (provider, model) in choices):
            raise OnDemandRemoteAIError("invalid_remote_model_allowlist", 422)
        choices.add((provider, model))
    return frozenset(choices)


class LocalOnDemandRemoteAI:
    def __init__(self, database, *, runner=None, additional_runners=None,
                 model_allowlist=None, daily_attempt_limit=MAX_DAILY_ATTEMPTS):
        if additional_runners is None:
            additional_runners = {}
        if (type(additional_runners) is not dict
                or any(k not in ("anthropic_messages", "gemini_generate_content")
                       or not callable(v) for k, v in additional_runners.items())
                or (runner is not None and not callable(runner))
                or (runner is None and not additional_runners)):
            raise OnDemandRemoteAIError("remote_runner_required", 503)
        if (type(daily_attempt_limit) is not int
                or not 1 <= daily_attempt_limit <= MAX_DAILY_ATTEMPTS):
            raise OnDemandRemoteAIError("invalid_remote_daily_attempt_limit", 422)
        self.model_allowlist = validate_model_allowlist(model_allowlist)
        self.daily_attempt_limit = daily_attempt_limit
        self.runner = runner
        self.additional_runners = dict(additional_runners)
        enabled = set((["openai_responses"] if runner is not None else [])
                      + list(additional_runners))
        if (self.model_allowlist is not None and not
                any(provider in enabled for provider, _ in self.model_allowlist)):
            raise OnDemandRemoteAIError(
                "remote_model_allowlist_has_no_enabled_provider", 422)
        self.model_safety = None
        self.agent_gate = None
        self.agent_state_lock = None
        self.lock = threading.RLock()
        location = str(database)
        if location != ":memory:":
            path = Path(database).expanduser()
            if path.is_symlink() or path.parent.is_symlink():
                raise OnDemandRemoteAIError("unsafe_remote_ask_database", 503)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == "posix":
                os.chmod(path.parent, 0o700)
            if path.exists():
                if not path.is_file() or (os.name == "posix"
                        and stat.S_IMODE(path.stat().st_mode) & 0o077):
                    raise OnDemandRemoteAIError("unsafe_remote_ask_database", 503)
            else:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            location = str(path)
        self.db = sqlite3.connect(location, timeout=5, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA journal_mode=DELETE")
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS remote_asks (
                request_id TEXT PRIMARY KEY,
                prompt_sha256 TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                answer TEXT,
                usage_json TEXT,
                created_utc TEXT NOT NULL,
                finished_utc TEXT,
                provider TEXT NOT NULL DEFAULT 'openai_responses'
            )""")
            cols = {x[1] for x in self.db.execute(
                "PRAGMA table_info(remote_asks)").fetchall()}
            if "provider" not in cols:
                # Historical rows remain OpenAI, never relabel them as Gemini.
                self.db.execute("""ALTER TABLE remote_asks
                    ADD COLUMN provider TEXT NOT NULL DEFAULT 'openai_responses'""")
            # A process crash after a paid POST may have reached the provider.
            # Neither a restart nor a fresh HTTP session may replay that id.
            self.db.execute("""UPDATE remote_asks
                SET status='OUTCOME_UNCERTAIN'
                WHERE status='IN_FLIGHT'""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS remote_one_shot_policy (
                id INTEGER PRIMARY KEY CHECK(id=1),
                approved_models_json TEXT NOT NULL,
                daily_attempt_limit INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                updated_utc TEXT NOT NULL
            )""")
            row = self.db.execute("""SELECT approved_models_json,
                daily_attempt_limit,revision FROM remote_one_shot_policy
                WHERE id=1""").fetchone()
            if row is None:
                if self.model_allowlist is not None or self.daily_attempt_limit < MAX_DAILY_ATTEMPTS:
                    self.db.execute("""INSERT INTO remote_one_shot_policy
                       (id,approved_models_json,daily_attempt_limit,revision,updated_utc)
                       VALUES (1,?,?,1,?)""",
                       (json.dumps(sorted(p+":"+m for p,m in self.model_allowlist))
                        if self.model_allowlist is not None else "null",
                        self.daily_attempt_limit,self._utcnow()))
            else:
                try:
                    stored_raw=json.loads(row[0])
                    stored=validate_model_allowlist(stored_raw)
                    stored_cap=row[1]
                    if (type(stored_cap) is not int
                            or not 1 <= stored_cap <= MAX_DAILY_ATTEMPTS
                            or type(row[2]) is not int or row[2] < 1):
                        raise ValueError
                except (ValueError,TypeError,OnDemandRemoteAIError):
                    raise OnDemandRemoteAIError(
                        "remote_policy_storage_invalid",503) from None
                if (self.model_allowlist is not None
                        and self.model_allowlist != stored):
                    raise OnDemandRemoteAIError(
                        "remote_policy_change_requires_offline_owner_review",409)
                if (self.daily_attempt_limit != MAX_DAILY_ATTEMPTS
                        and self.daily_attempt_limit != stored_cap):
                    raise OnDemandRemoteAIError(
                        "remote_policy_change_requires_offline_owner_review",409)
                # Never silently relax a saved policy on restart when launcher
                # omitted the flag. Explicit offline owner review is required.
                self.model_allowlist = stored
                self.daily_attempt_limit = stored_cap
        enabled = set((["openai_responses"] if self.runner is not None else [])
                      + list(self.additional_runners))
        if self.model_allowlist is not None and not any(
                p in enabled for p,_ in self.model_allowlist):
            raise OnDemandRemoteAIError(
                "remote_policy_has_no_enabled_provider",403)

    def attach_gate(self, model_safety, agent_gate, agent_state_lock):
        if model_safety is None or agent_gate is None or agent_state_lock is None:
            raise OnDemandRemoteAIError("model_safety_gate_required", 503)
        self.model_safety = model_safety
        self.agent_gate = agent_gate
        self.agent_state_lock = agent_state_lock

    @staticmethod
    def validate(request):
        if (type(request) is not dict
                or set(request) not in (
                    {"request_id", "model", "prompt", "paid_api_consent"},
                    {"request_id", "model", "prompt", "paid_api_consent", "provider"})):
            raise OnDemandRemoteAIError("invalid_remote_ask_fields")
        if request.get("provider", "openai_responses") not in (
                "openai_responses", "anthropic_messages", "gemini_generate_content"):
            raise OnDemandRemoteAIError("invalid_remote_provider")
        if request["paid_api_consent"] is not True:
            raise OnDemandRemoteAIError("paid_api_consent_required", 403)
        if type(request["request_id"]) is not str or not REQUEST_ID.fullmatch(request["request_id"]):
            raise OnDemandRemoteAIError("invalid_remote_ask_id")
        if (not valid_remote_model_name(request["model"])
                or (request.get("provider") == "anthropic_messages"
                    and ANTHROPIC_MODEL.fullmatch(request["model"]) is None)
                or (request.get("provider") == "gemini_generate_content"
                    and GEMINI_MODEL.fullmatch(request["model"]) is None)):
            raise OnDemandRemoteAIError("invalid_remote_model_name")
        prompt = request["prompt"]
        if type(prompt) is not str or not 1 <= len(prompt.strip()) <= MAX_PROMPT:
            raise OnDemandRemoteAIError("invalid_remote_ask_prompt")
        return request

    @staticmethod
    def _utcnow():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _public_receipt(self, row):
        rid, model, status, answer, usage_json, created, finished, provider = row
        return {
            "request_id": rid,
            "model": model,
            "provider": provider,
            "status": status,
            "answer": answer if status == "COMPLETED" else None,
            "usage_tokens": json.loads(usage_json) if usage_json else None,
            "created_utc": created,
            "finished_utc": finished,
            "external_dispatch_count": 0,
            "provider_billing_independently_verified": False,
            "automatic_retry_allowed": False,
        }

    def receipt(self, request_id):
        if type(request_id) is not str or not REQUEST_ID.fullmatch(request_id):
            raise OnDemandRemoteAIError("invalid_remote_ask_id")
        with self.lock:
            row = self.db.execute(
                """SELECT request_id,model,status,answer,usage_json,
                          created_utc,finished_utc,provider
                   FROM remote_asks WHERE request_id=?""",
                (request_id,)).fetchone()
        if row is None:
            raise OnDemandRemoteAIError("remote_ask_not_found", 404)
        return self._public_receipt(row)

    def bound_receipt(self, request_id, model, prompt, *, provider="openai_responses"):
        """Read a receipt only if the caller reproduces the exact paid request.

        This is an internal binding check; not an HTTP endpoint. Prevents
        importing some unrelated paid answer as a plan for a different goal.
        """
        if type(request_id) is not str or not REQUEST_ID.fullmatch(request_id):
            raise OnDemandRemoteAIError("invalid_remote_ask_id")
        if provider not in PROVIDERS:
            raise OnDemandRemoteAIError("invalid_remote_provider")
        if (not valid_remote_model_name(model)
                or (provider == "anthropic_messages"
                    and ANTHROPIC_MODEL.fullmatch(model) is None)
                or (provider == "gemini_generate_content"
                    and GEMINI_MODEL.fullmatch(model) is None)):
            raise OnDemandRemoteAIError("invalid_remote_model_name")
        if type(prompt) is not str or not 1 <= len(prompt.strip()) <= MAX_PROMPT:
            raise OnDemandRemoteAIError("invalid_remote_ask_prompt")
        from hmac import compare_digest
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        with self.lock:
            row = self.db.execute(
                """SELECT prompt_sha256,model,provider FROM remote_asks
                   WHERE request_id=?""", (request_id,)
            ).fetchone()
            if row is None:
                raise OnDemandRemoteAIError("remote_ask_not_found", 404)
            if (row[1] != model or row[2] != provider
                    or not compare_digest(row[0], digest)):
                raise OnDemandRemoteAIError("remote_ask_binding_mismatch", 409)
            # Keep the database lock until the entire receipt is read.
            return self.receipt(request_id)

    def recent(self):
        with self.lock:
            rows = self.db.execute(
                """SELECT request_id,model,status,answer,usage_json,
                          created_utc,finished_utc,provider
                   FROM remote_asks ORDER BY created_utc DESC,request_id DESC LIMIT 15"""
            ).fetchall()
        return {
            "enabled": True,
            "enabled_providers": self.enabled_providers(),
            "maximum_attempts_per_utc_day": self.daily_attempt_limit,
            "remote_model_allowlist_enforced": self.model_allowlist is not None,
            "approved_models": self.approved_models(),
            "maximum_persisted_attempts": MAX_ROWS,
            # Never reveal stored response text in a summary list. An owner
            # must deliberately select one receipt after authentication.
            "receipts": [
                {k:v for k,v in self._public_receipt(row).items() if k != "answer"}
                for row in rows
            ],
        }

    def approved_models(self):
        if self.model_allowlist is None:
            return None
        enabled = (["openai_responses"] if self.runner is not None else []) + list(
            self.additional_runners)
        return [{"provider": provider, "model": model}
                for provider, model in sorted(self.model_allowlist)
                if provider in enabled]

    def enabled_providers(self):
        providers = (["openai_responses"] if self.runner is not None else []) + sorted(
            self.additional_runners)
        if self.model_allowlist is None:
            return providers
        return [provider for provider in providers
                if any(p == provider for p, _ in self.model_allowlist)]

    def _claim(self, request):
        now = self._utcnow()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if self.db.execute(
                    "SELECT 1 FROM remote_asks WHERE request_id=?",
                    (request["request_id"],)).fetchone():
                    raise OnDemandRemoteAIError(
                        "remote_ask_id_already_used_check_receipt", 409)
                if self.db.execute("SELECT COUNT(*) FROM remote_asks").fetchone()[0] >= MAX_ROWS:
                    raise OnDemandRemoteAIError("remote_ask_capacity_reached", 409)
                if self.db.execute(
                    "SELECT COUNT(*) FROM remote_asks WHERE substr(created_utc,1,10)=?",
                    (now[:10],)).fetchone()[0] >= self.daily_attempt_limit:
                    raise OnDemandRemoteAIError("remote_ask_daily_cap_reached", 429)
                self.db.execute(
                    """INSERT INTO remote_asks(request_id,prompt_sha256,model,
                       provider,status,created_utc)
                       VALUES(?,?,?, ?,'IN_FLIGHT',?)""",
                    (request["request_id"],
                     hashlib.sha256(request["prompt"].encode("utf-8")).hexdigest(),
                     request["model"], request.get("provider", "openai_responses"),
                     now))
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def _finalize(self, request_id, status, answer=None, usage=None):
        with self.lock, self.db:
            changed = self.db.execute(
                """UPDATE remote_asks
                   SET status=?,answer=?,usage_json=?,finished_utc=?
                   WHERE request_id=? AND status='IN_FLIGHT'""",
                (status, answer,
                 json.dumps(usage, sort_keys=True) if usage is not None else None,
                 self._utcnow(), request_id)).rowcount
            if changed != 1:
                raise OnDemandRemoteAIError("remote_ask_ledger_uncertain", 503)

    @staticmethod
    def _validate_result(result, provider="openai_responses"):
        if (type(result) is not dict
                or result.get("mode") != {
                    "openai_responses": "REMOTE_OPENAI_READ_ONLY",
                    "anthropic_messages": "REMOTE_ANTHROPIC_READ_ONLY",
                    "gemini_generate_content": "REMOTE_GEMINI_READ_ONLY",
                }.get(provider)
                or result.get("external_dispatch_count") != 0
                or result.get("tools_used") != []
                or type(result.get("answer")) is not str
                or not 1 <= len(result["answer"].strip()) <= MAX_ANSWER):
            raise RemoteInferenceError("remote_response_uncertain")
        usage = result.get("usage")
        if (type(usage) is not dict
                or set(usage) != {"input_tokens", "output_tokens"}
                or type(usage["input_tokens"]) is not int
                or not 0 <= usage["input_tokens"] <= 200000
                or type(usage["output_tokens"]) is not int
                or not 0 <= usage["output_tokens"] <= MAX_OUTPUT_TOKENS):
            raise RemoteInferenceError("remote_response_uncertain")
        return result

    def run(self, request):
        self.validate(request)
        if self.model_safety is None or self.agent_gate is None:
            raise OnDemandRemoteAIError("model_safety_gate_required", 503)
        rid = request["request_id"]
        provider = request.get("provider", "openai_responses")
        runner = (self.runner if provider == "openai_responses"
                  else self.additional_runners.get(provider))
        if runner is None:
            raise OnDemandRemoteAIError("remote_provider_not_enabled", 403)
        if (self.model_allowlist is not None
                and (provider, request["model"]) not in self.model_allowlist):
            # Refuse before model-safety claim, payment dispatch or idempotency
            # ledger insertion. Never silently select an alternate model.
            raise OnDemandRemoteAIError("remote_model_not_owner_allowlisted", 403)
        # Checking before acquiring the model gate gives duplicate callers a
        # clear receipt lookup path even if the first POST is still running.
        with self.lock:
            if self.db.execute(
                "SELECT 1 FROM remote_asks WHERE request_id=?", (rid,)
            ).fetchone():
                raise OnDemandRemoteAIError(
                    "remote_ask_id_already_used_check_receipt", 409)
        acquired = False
        started = False
        completed = False
        uncertain = False
        dispatched = False
        try:
            with self.agent_state_lock:
                acquired = self.agent_gate.acquire(blocking=False)
                if not acquired:
                    raise OnDemandRemoteAIError("model_operation_busy", 429)
                try:
                    self.model_safety.begin()
                    started = True
                except ModelSafetyError as exc:
                    raise OnDemandRemoteAIError(
                        "model_safety_quarantined"
                        if str(exc) == "model_safety_quarantined"
                        else "model_safety_storage_unavailable",
                        423 if str(exc) == "model_safety_quarantined" else 503
                    ) from None
            # Durable idempotency claim must complete BEFORE sending the POST.
            self._claim(request)
            try:
                dispatched = True
                result = self._validate_result(
                    runner(request["prompt"].strip(), request["model"]), provider)
                self._finalize(rid, "COMPLETED", result["answer"], result["usage"])
                completed = True
            except RemoteInferenceError as exc:
                if str(exc) == "remote_provider_rejected":
                    self._finalize(rid, "FAILED_DEFINITE")
                    completed = True
                    raise OnDemandRemoteAIError("remote_provider_rejected", 503) from None
                uncertain = True
                self._finalize(rid, "OUTCOME_UNCERTAIN")
                raise OnDemandRemoteAIError(
                    "remote_result_uncertain_check_usage_and_receipt", 503) from None
            except OnDemandRemoteAIError:
                raise
            except BaseException:
                uncertain = True
                # Could be a provider success followed by a local storage error.
                try:
                    self._finalize(rid, "OUTCOME_UNCERTAIN")
                except (sqlite3.Error, OnDemandRemoteAIError):
                    pass
                raise OnDemandRemoteAIError(
                    "remote_result_uncertain_check_usage_and_receipt", 503) from None
            try:
                self.model_safety.finish()
                started = False
            except ModelSafetyError:
                raise OnDemandRemoteAIError(
                    "model_safety_storage_unavailable_check_receipt", 503) from None
            return self.receipt(rid)
        finally:
            if started:
                try:
                    if uncertain or (dispatched and not completed):
                        self.model_safety.timeout()
                    else:
                        self.model_safety.finish()
                except ModelSafetyError:
                    pass
            if acquired:
                self.agent_gate.release()

    def close(self):
        with self.lock:
            self.db.close()
