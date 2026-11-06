"""No real Google login/email traffic: deterministic OAuth, vault and Gmail tests."""
import base64
import http.cookiejar
from http.server import ThreadingHTTPServer
import threading
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, HTTPCookieProcessor, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tools"))

from local_connector_vault import EncryptedConnectorVault, VaultError, generate_master_key
from local_google_gmail import (
    GmailConnectorError, GoogleGmailMetadata, GMAIL_SCOPE, GMAIL_AAD,
    GMAIL_BASE, TOKEN_ENDPOINT, _official_json)
from connect_google_gmail import authorize_url, exchange_code, pkce

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
CLIENT = "123456789012-abcdefghijk12345.apps.googleusercontent.com"
REFRESH = "refresh_token_" + "z" * 48
ACCESS = "access_token_" + "y" * 40


@unittest.skipUnless(HAS_CRYPTO, "Dedicated Gmail crypto CI installs optional dependency")
class NativeGmailConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "operator" / "google-gmail-vault.json"
        self.key = generate_master_key()
        self.calls = []

        def transport(url, form, bearer):
            self.calls.append((url, form, bearer))
            if url == TOKEN_ENDPOINT:
                return {"access_token": ACCESS, "scope": GMAIL_SCOPE}
            if url.endswith("/profile"):
                return {"emailAddress": "owner@example.org",
                        "messagesTotal": 125, "threadsTotal": 110}
            if "/messages?" in url:
                return {"messages": [{"id": "abc123", "threadId": "thread"}]}
            if "/messages/abc123?" in url:
                return {"payload": {"headers": [
                    {"name": "From", "value": "Contact <contact@example.org>"},
                    {"name": "Subject", "value": "A scoped subject"},
                    {"name": "Date", "value": "Fri, 6 Nov 2026 10:00:00 GMT"}]},
                    "labelIds": ["INBOX", "UNREAD"]}
            raise AssertionError("Unexpected URL: " + url)

        self.gmail = GoogleGmailMetadata(self.path, self.key, transport=transport)
        self.transport = transport

    def tearDown(self):
        self.tmp.cleanup()

    def enroll(self):
        self.gmail.save_grant(CLIENT, "", REFRESH)

    def test_initial_status_does_not_create_grant_or_make_network_call(self):
        self.assertFalse(self.gmail.status()["connected"])
        self.assertFalse(self.path.exists())
        self.assertEqual(self.calls, [])

    def test_google_refresh_token_is_encrypted_separate_domain(self):
        self.enroll()
        self.assertTrue(self.gmail.status()["connected"])
        contents = self.path.read_text()
        self.assertNotIn(REFRESH, contents)
        self.assertNotIn(CLIENT, contents)
        self.assertNotIn("google_gmail", contents)
        with self.assertRaises(VaultError):
            EncryptedConnectorVault(self.path, self.key).read()
        raw = EncryptedConnectorVault(self.path, self.key, aad=GMAIL_AAD).read()
        self.assertEqual(raw["refresh_token"], REFRESH)

    def test_wrong_master_key_refused_without_legacy_fallback(self):
        self.enroll()
        wrong = GoogleGmailMetadata(self.path, generate_master_key(), transport=self.transport)
        with self.assertRaises(GmailConnectorError):
            wrong.status()
        with self.assertRaises(GmailConnectorError):
            wrong.save_grant(CLIENT, "", REFRESH, replace=True)

    def test_one_user_authorized_profile_only_reads_fixed_google_host(self):
        self.enroll()
        self.assertEqual(self.gmail.profile()["email"], "owner@example.org")
        self.assertEqual(self.gmail.profile()["messages_total"], 125)
        self.assertTrue(all(x[0].startswith("https://") for x in self.calls))
        self.assertTrue(all(x[0] in (TOKEN_ENDPOINT, GMAIL_BASE + "/profile")
                            for x in self.calls))
        self.assertNotIn(REFRESH, json.dumps(self.gmail.profile()))

    def test_five_inbox_headers_only_no_body_and_no_send(self):
        self.enroll()
        d = self.gmail.inbox()
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["messages"][0]["subject"], "A scoped subject")
        self.assertTrue(d["messages"][0]["unread"])
        self.assertNotIn("body", json.dumps(d).lower())
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all(x[1] is None for x in self.calls[1:]))

    def test_missing_scope_rejected_before_mailbox_fetch(self):
        self.enroll()
        def bad(url, form, bearer):
            return {"access_token": ACCESS,
                    "scope": "https://www.googleapis.com/auth/calendar.readonly"}
        other = GoogleGmailMetadata(self.path, self.key, transport=bad)
        with self.assertRaisesRegex(GmailConnectorError, "google_scope_not_granted"):
            other.profile()

    def test_bad_grant_cannot_overwrite_existing_without_explicit_replace(self):
        self.enroll()
        with self.assertRaises(GmailConnectorError):
            self.gmail.save_grant(CLIENT, "", REFRESH)
        self.gmail.save_grant(CLIENT, "", REFRESH + "1", replace=True)
        self.assertEqual(self.gmail._grant()["refresh_token"], REFRESH + "1")

    def test_bad_google_message_id_blocks_followup_fetch(self):
        self.enroll()
        def malicious(url, form, bearer):
            if url == TOKEN_ENDPOINT:
                return {"access_token": ACCESS}
            return {"messages": [{"id": "../token"}]}
        other = GoogleGmailMetadata(self.path, self.key, transport=malicious)
        with self.assertRaises(GmailConnectorError):
            other.inbox()

    def test_invalid_client_id_and_secrets_rejected(self):
        with self.assertRaises(GmailConnectorError):
            self.gmail.save_grant("not-google-client", "", REFRESH)
        with self.assertRaises(GmailConnectorError):
            self.gmail.save_grant(CLIENT, "", "short")

    def test_oauth_pkce_s256_state_and_narrow_scope(self):
        verifier, challenge = pkce()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertEqual(challenge,
                         base64.urlsafe_b64encode(hashlib.sha256(
                             verifier.encode()).digest()).decode().rstrip("="))
        url = authorize_url(CLIENT, "http://127.0.0.1:49152/oauth/callback",
                            "strong-random-state", challenge)
        args = parse_qs(urlsplit(url).query)
        self.assertEqual(args["code_challenge_method"], ["S256"])
        self.assertEqual(args["state"], ["strong-random-state"])
        self.assertEqual(args["scope"], [GMAIL_SCOPE])
        self.assertEqual(args["access_type"], ["offline"])
        self.assertNotIn("gmail.send", url)

    def test_oauth_exchange_rejects_missing_refresh_token_and_scope(self):
        with self.assertRaises(GmailConnectorError):
            exchange_code(CLIENT, "", "authcode", "verifier", "http://127.0.0.1:1",
                          lambda *args: {"scope": GMAIL_SCOPE})
        with self.assertRaises(GmailConnectorError):
            exchange_code(CLIENT, "", "authcode", "verifier", "http://127.0.0.1:1",
                          lambda *args: {"scope": "https://www.googleapis.com/auth/gmail.send",
                                         "refresh_token": REFRESH})
        self.assertEqual(exchange_code(
            CLIENT, "", "authcode", "verifier", "http://127.0.0.1:1",
            lambda *args: {"scope": GMAIL_SCOPE, "refresh_token": REFRESH}), REFRESH)

    def test_unapproved_endpoint_never_opens_network_connection(self):
        for url in ("http://gmail.googleapis.com/", "https://evil.example/token",
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"):
            with self.assertRaises(GmailConnectorError):
                _official_json(url, bearer=ACCESS)


@unittest.skipUnless(HAS_CRYPTO, "Dedicated Gmail crypto CI installs optional dependency")
class NativeGmailHttpAuthTests(unittest.TestCase):
    def test_actual_owner_session_csrf_password_and_redacted_audit(self):
        from local_operator_auth import LocalOperatorAuth
        from local_security_audit import LocalSecurityAudit
        from local_server import create_handler
        from local_workflow_core import LocalWorkflowCore

        with tempfile.TemporaryDirectory(prefix="gmail-http-") as root:
            folder = Path(root)
            password = "DisposableStudioGoogleOwner2026!"
            operator = LocalOperatorAuth.provision(folder / "operator.json", password)
            # provision() may return None; load an actual authenticated verifier.
            if operator is None:
                operator = LocalOperatorAuth.load(folder / "operator.json")
            audit = LocalSecurityAudit(folder / "security-audit.sqlite3")
            core = LocalWorkflowCore(folder / "workflow.sqlite3")
            key = generate_master_key()
            grant = GoogleGmailMetadata(
                folder / "google-vault.json", key,
                transport=lambda url, form, bearer: (
                    {"access_token": ACCESS, "scope": GMAIL_SCOPE}
                    if url == TOKEN_ENDPOINT else
                    {"emailAddress": "owner@example.org",
                     "messagesTotal": 5, "threadsTotal": 4}))
            grant.save_grant(CLIENT, "", REFRESH)
            server = ThreadingHTTPServer(("127.0.0.1", 0),
                create_handler(local_core=core, operator_auth=operator,
                               security_audit=audit, google_gmail=grant))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            opener = build_opener(
                ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
            base = "http://127.0.0.1:" + str(server.server_port)

            def post(path, body, csrf=None):
                headers = {"Content-Type": "application/json"}
                if csrf is not None:
                    headers["X-AAS-CSRF"] = csrf
                req = Request(base + path, json.dumps(body).encode(),
                              headers=headers, method="POST")
                with opener.open(req, timeout=4) as response:
                    return json.load(response)

            try:
                with self.assertRaises(HTTPError) as unauth:
                    opener.open(base + "/api/google-gmail-status", timeout=4)
                self.assertEqual(unauth.exception.code, 401)

                signed_in = post("/api/operator-login", {"password": password})
                csrf = signed_in["csrf_token"]
                with opener.open(base + "/api/google-gmail-status", timeout=4) as response:
                    self.assertTrue(json.load(response)["connected"])
                with self.assertRaises(HTTPError) as no_csrf:
                    post("/api/google-gmail-profile", {"current_password": password})
                self.assertEqual(no_csrf.exception.code, 403)

                with self.assertRaises(HTTPError) as bad_password:
                    post("/api/google-gmail-profile", {"current_password": "wrong"}, csrf)
                self.assertEqual(bad_password.exception.code, 401)

                result = post("/api/google-gmail-profile",
                              {"current_password": password}, csrf)
                self.assertEqual(result["email"], "owner@example.org")
                self.assertEqual(result["messages_total"], 5)
                events = audit.recent()
                self.assertTrue(any(e["action"] == "GOOGLE_GMAIL_READ" and
                                    e["outcome"] == "SUCCESS" for e in events))
                self.assertNotIn("owner@example.org", json.dumps(events))
                self.assertNotIn(REFRESH, json.dumps(events))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                audit.close()
                core.close()
