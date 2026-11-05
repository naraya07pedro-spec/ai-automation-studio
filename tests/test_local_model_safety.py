"""Slice 64: genuine SQLite persistence and restart HTTP safety fixtures.

These tests do NOT run Ollama. They verify the exact durability boundary.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_model_safety import LocalModelSafetyState, ModelSafetyError
from local_operator_auth import LocalOperatorAuth
from local_ollama_agent import AgentError
from local_server import create_handler

PASSWORD = "fixture_only_durable_safety_owner_password"


class ModelSafetyPersistenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.filename = Path(temporary.name) / "ollama-safety.sqlite3"

    def test_inflight_checkpoint_survives_abrupt_process_death(self):
        first = LocalModelSafetyState(self.filename)
        self.assertEqual("IDLE", first.phase())
        self.assertEqual("IN_FLIGHT", first.begin())
        first.close()  # Simulate a crash without normal finish.
        recovered = LocalModelSafetyState(self.filename)
        try:
            self.assertEqual("UNCERTAIN", recovered.phase())
            with self.assertRaisesRegex(ModelSafetyError, "model_safety_quarantined"):
                recovered.begin()
            self.assertEqual("UNCERTAIN", recovered.finish())
            self.assertEqual("UNCERTAIN", recovered.phase())
            self.assertEqual("IDLE", recovered.acknowledge())
            self.assertEqual("IN_FLIGHT", recovered.begin())
            self.assertEqual("IDLE", recovered.finish())
        finally:
            recovered.close()

    def test_normal_completion_and_timeout_across_restarts(self):
        store = LocalModelSafetyState(self.filename)
        store.begin()
        store.finish()
        store.close()
        another = LocalModelSafetyState(self.filename)
        self.assertEqual("IDLE", another.phase())
        another.begin()
        another.timeout()
        another.finish()  # Must not clear a timeout.
        another.close()
        third = LocalModelSafetyState(self.filename)
        try:
            self.assertEqual("UNCERTAIN", third.phase())
            with self.assertRaisesRegex(ModelSafetyError, "model_safety_quarantined"):
                third.begin()
            self.assertEqual("IDLE", third.acknowledge())
            self.assertEqual("IN_FLIGHT", third.begin())
            with self.assertRaisesRegex(ModelSafetyError, "model_safety_active"):
                third.acknowledge()
            third.finish()
        finally:
            third.close()

    def test_cross_instance_access_cannot_overwrite_active_checkpoint(self):
        owner = LocalModelSafetyState(self.filename)
        owner.begin()
        # Reopening while another instance might still own the checkpoint
        # must fail safe, never reset it to IDLE.
        observer = LocalModelSafetyState(self.filename)
        try:
            self.assertEqual("UNCERTAIN", observer.phase())
            with self.assertRaises(ModelSafetyError):
                observer.begin()
        finally:
            observer.close()
            owner.close()

    def test_failure_to_open_corrupt_or_unsafe_state_never_resets(self):
        self.filename.write_bytes(b"not SQLite")
        if os.name == "posix":
            # The corrupt-file case must pass the independent permission gate.
            self.filename.chmod(0o600)
        with self.assertRaisesRegex(ModelSafetyError, "model_safety_storage_unavailable"):
            LocalModelSafetyState(self.filename)
        if os.name == "posix":
            self.filename.chmod(0o644)
            with self.assertRaisesRegex(ModelSafetyError,
                                        "insecure_model_safety_permissions"):
                LocalModelSafetyState(self.filename)
        self.filename.unlink()
        actual = self.filename.with_name("actual.sqlite3")
        actual.write_text("not a database")
        try:
            self.filename.symlink_to(actual)
        except (OSError, NotImplementedError):
            return  # Filesystem policy prohibits symlinks on this runner.
        with self.assertRaisesRegex(ModelSafetyError, "unsafe_model_safety_path"):
            LocalModelSafetyState(self.filename)

    def test_only_minimal_enumerated_state_is_stored(self):
        state = LocalModelSafetyState(self.filename)
        state.begin()
        state.timeout()
        state.close()
        with sqlite3.connect(self.filename) as conn:
            columns = [c[1] for c in conn.execute(
                "PRAGMA table_info(model_safety)").fetchall()]
            record = conn.execute("SELECT * FROM model_safety").fetchall()
        self.assertEqual(["id", "phase"], columns)
        self.assertEqual([(1, "UNCERTAIN")], record)


class ModelSafetyRestartHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "ollama-safety.sqlite3"
        self.auth = LocalOperatorAuth.ephemeral_for_tests(PASSWORD)
        _, session = self.auth.login(PASSWORD)
        self.cookie = "aas_session=" + session["cookie_token"]
        self.csrf = session["csrf_token"]
        self.dispatched = []
        self.session = None

    def _start(self):
        state = LocalModelSafetyState(self.path)
        def acceptance(model):
            self.dispatched.append(model)
            if model == "fixture:timeout":
                raise AgentError("local_model_timeout")
            if model == "fixture:disconnect":
                raise AgentError("local_model_transport_uncertain")
            return {"status": "SYNTHETIC_MODEL_SMOKE_PASSED",
                    "model": model, "source_evidence_verified": True,
                    "model_calls": 1, "external_write_count": 0,
                    "history_saved": False}
        server = ThreadingHTTPServer(("127.0.0.1", 0),
            create_handler(operator_auth=self.auth, model_safety_state=state,
                           local_agent_runner=lambda prompt,model: {
                               "mode": "LOCAL_MODEL", "answer": "fixture",
                               "tools_used": [], "external_dispatch_count": 0},
                           local_device_acceptance=acceptance))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.session = (state, server, worker)
        return server

    def _stop(self):
        if self.session is not None:
            state, server, worker = self.session
            server.shutdown()
            server.server_close()
            worker.join(4)
            state.close()
            self.session = None

    def tearDown(self):
        self._stop()

    def _request(self, method, path, body=None, auth=True, csrf=True):
        server = self.session[1]
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port,
                                          timeout=5)
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Cookie"] = self.cookie
        if csrf:
            headers["X-AAS-CSRF"] = self.csrf
        conn.request(method, path,
                     body=json.dumps(body).encode() if body is not None else None,
                     headers=headers)
        response = conn.getresponse()
        result = (response.status, json.loads(response.read()))
        conn.close()
        return result

    def test_uncertain_connection_loss_remains_blocked_after_restart(self):
        self._start()
        api="/api/local-agent-device-acceptance"
        code,body=self._request("POST",api,{"model":"fixture:disconnect"})
        self.assertEqual(503,code)
        self.assertEqual("local_model_transport_uncertain",body["error"])
        self._stop()
        self._start()
        code,status=self._request("GET","/api/local-agent-timeout-status")
        self.assertEqual(200,code)
        self.assertEqual("UNCERTAIN",status["checkpoint_state"])
        self.assertFalse(status["backend_stopped_verified"])
        self.assertEqual(423,self._request("POST",api,
                                           {"model":"fixture:model"})[0])
        self.assertEqual(["fixture:disconnect"],self.dispatched)
        self.assertEqual(200,self._request(
            "POST","/api/local-agent-timeout-acknowledge",
            {"acknowledge":"OLLAMA_INSPECTED"})[0])
        self.assertEqual(200,self._request("POST",api,
                                           {"model":"fixture:model"})[0])
        self.assertEqual(["fixture:disconnect","fixture:model"],self.dispatched)

    def test_server_restart_preserves_timeout_quarantine(self):
        self._start()
        api = "/api/local-agent-device-acceptance"
        code, output = self._request("POST", api, {"model": "fixture:timeout"})
        self.assertEqual((503, "local_model_timeout"),
                         (code, output["error"]))
        self._stop()
        self._start()
        code, status = self._request("GET", "/api/local-agent-timeout-status")
        self.assertEqual(200, code)
        self.assertEqual("UNCERTAIN", status["checkpoint_state"])
        self.assertTrue(status["timeout_unresolved"])
        self.assertFalse(status["backend_stopped_verified"])
        self.assertEqual(423, self._request("POST", api,
                                           {"model": "fixture:model"})[0])
        self.assertEqual(["fixture:timeout"], self.dispatched)
        acknowledge = "/api/local-agent-timeout-acknowledge"
        self.assertEqual(401, self._request("POST", acknowledge,
                         {"acknowledge": "OLLAMA_INSPECTED"}, auth=False)[0])
        self.assertEqual(403, self._request("POST", acknowledge,
                         {"acknowledge": "OLLAMA_INSPECTED"}, csrf=False)[0])
        self.assertEqual(200, self._request("POST", acknowledge,
                         {"acknowledge": "OLLAMA_INSPECTED"})[0])
        self.assertEqual(200, self._request("POST", api,
                                           {"model": "fixture:model"})[0])
        self.assertEqual(["fixture:timeout", "fixture:model"], self.dispatched)
