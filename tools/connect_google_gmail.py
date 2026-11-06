"""Enroll a Google Desktop OAuth PKCE Gmail metadata grant on the OWNER'S PC.

Independent, one-shot browser consent. Does not contact Google until run.
A loopback browser callback is not reachable through the Studio web UI.
"""
import argparse
import base64
import getpass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from local_google_gmail import (
    GMAIL_SCOPE, TOKEN_ENDPOINT, GmailConnectorError,
    GoogleGmailMetadata, _official_json, _valid_client)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"


def pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


def authorize_url(client_id, redirect_uri, state, challenge):
    _valid_client(client_id)
    params = {"client_id": client_id, "redirect_uri": redirect_uri,
              "response_type": "code", "scope": GMAIL_SCOPE,
              "access_type": "offline", "prompt": "consent",
              "state": state, "code_challenge": challenge,
              "code_challenge_method": "S256"}
    return AUTH_ENDPOINT + "?" + urlencode(params)


class Callback(HTTPServer):
    def __init__(self, state):
        self.expected_state = state
        self.authorization_code = None
        self.denied = False
        super().__init__(("127.0.0.1", 0), CallbackHandler)
        self.timeout = 1


class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return  # Never log OAuth authorization codes in request URLs.

    def do_GET(self):
        parsed = urlsplit(self.path)
        expected_host = "127.0.0.1:" + str(self.server.server_port)
        params = parse_qs(parsed.query, keep_blank_values=True)
        good = (parsed.path == "/oauth/callback" and
                self.headers.get("Host") == expected_host and
                len(self.path) < 4096 and
                len(params.get("state", [])) == 1 and
                hmac.compare_digest(params["state"][0],
                                    self.server.expected_state))
        if not good:
            self._reply(400, "Invalid authorization callback.")
            return
        if len(params.get("error", [])) == 1:
            self.server.denied = True
            self._reply(200, "Authorization declined. Return to your terminal.")
            return
        codes = params.get("code", [])
        if len(codes) != 1 or not 8 <= len(codes[0]) <= 2048:
            self._reply(400, "Invalid authorization response.")
            return
        self.server.authorization_code = codes[0]
        self._reply(200, "Authorization received. Close this tab and return to Studio.")

    def _reply(self, status, message):
        body = ("<!doctype html><title>Studio OAuth</title><p>" +
                message + "</p>").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)


def exchange_code(client_id, client_secret, code, verifier, redirect_uri,
                  transport=_official_json):
    form = {"client_id": client_id, "code": code,
            "code_verifier": verifier, "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"}
    if client_secret:
        form["client_secret"] = client_secret
    try:
        answer = transport(TOKEN_ENDPOINT, form, None)
    except Exception:
        raise GmailConnectorError("google_oauth_exchange_failed") from None
    if type(answer) is not dict:
        raise GmailConnectorError("google_oauth_exchange_failed")
    granted = answer.get("scope")
    if type(granted) is not str or GMAIL_SCOPE not in granted.split():
        raise GmailConnectorError("google_metadata_scope_not_granted", 403)
    refresh = answer.get("refresh_token")
    if type(refresh) is not str or len(refresh) < 16:
        raise GmailConnectorError("google_offline_refresh_grant_not_returned")
    return refresh


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="One-time official Google Desktop OAuth connection for Gmail metadata only.")
    parser.add_argument("--client-id", default=os.getenv("AAS_GOOGLE_CLIENT_ID", ""))
    parser.add_argument("--replace", action="store_true",
                        help="Explicitly replace an existing, readable encrypted Gmail grant.")
    parser.add_argument("--disconnect-local", action="store_true",
                        help="Remove only the local encrypted grant, NOT Google's provider-side consent.")
    args = parser.parse_args(argv)
    master_key = os.getenv("AAS_VAULT_MASTER_KEY") or getpass.getpass(
        "Existing AAS vault master key (not echoed): ")
    gmail = GoogleGmailMetadata(
        Path.home() / ".ai-automation-studio" / "google-gmail-vault.json",
        master_key)
    if args.disconnect_local:
        if gmail.path.exists():
            gmail._grant()  # Refuse to delete a locked/wrong-key vault.
            gmail.path.unlink()
        print("Local Gmail grant removed. Revoke Google account access separately.")
        return 0
    client_secret = os.getenv("AAS_GOOGLE_CLIENT_SECRET", "")
    _valid_client(args.client_id, client_secret)
    if gmail.path.exists() and not args.replace:
        raise GmailConnectorError("google_grant_exists_use_explicit_replace", 409)
    if gmail.path.exists():
        gmail._grant()
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(32)
    with Callback(state) as receiver:
        redirect = "http://127.0.0.1:" + str(receiver.server_port) + "/oauth/callback"
        url = authorize_url(args.client_id, redirect, state, challenge)
        print("Opening the official Google consent page for Gmail METADATA ONLY.")
        print("If a browser did not open, copy the Google authorization URL:")
        print(url)
        webbrowser.open(url)
        deadline = time.monotonic() + 150
        while (time.monotonic() < deadline and
               receiver.authorization_code is None and not receiver.denied):
            receiver.handle_request()
        if receiver.denied:
            raise GmailConnectorError("google_consent_declined", 403)
        if receiver.authorization_code is None:
            raise GmailConnectorError("google_oauth_callback_timeout")
        code = receiver.authorization_code
    refresh = exchange_code(args.client_id, client_secret, code, verifier, redirect)
    gmail.save_grant(args.client_id, client_secret, refresh, replace=args.replace)
    print("Gmail metadata-only OAuth grant stored in separate encrypted AES-GCM vault.")
    print("No Gmail content, authorization code, refresh token or master key printed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GmailConnectorError, ValueError) as exc:
        raise SystemExit("Gmail OAuth enrollment blocked: " + str(exc)) from None
