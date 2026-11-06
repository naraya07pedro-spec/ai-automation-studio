"""Explicitly opted-in Google OAuth Desktop / Gmail METADATA-only connector.

Never a model tool, background poller, Gmail sender or all-account plugin bridge.
The OAuth refresh grant is sealed in a separate AES-GCM domain, with the
master key supplied independently. Only fixed official Google endpoints are
called, never URLs obtained from responses, users or the model.
"""
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPRedirectHandler, Request

from local_connector_vault import EncryptedConnectorVault, VaultError, decode_master_key

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.metadata"
GMAIL_AAD = b"AI-AUTOMATION-STUDIO:GOOGLE-GMAIL-METADATA:V1"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_RESPONSE = 65536


class GmailConnectorError(RuntimeError):
    def __init__(self, reason, status=503):
        super().__init__(reason)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _official_json(url, form=None, bearer=None):
    """Do not follow redirects carrying bearer credentials."""
    if not (url == TOKEN_ENDPOINT or url == GMAIL_BASE + "/profile"
            or url.startswith(GMAIL_BASE + "/messages?")
            or url.startswith(GMAIL_BASE + "/messages/")):
        raise GmailConnectorError("google_endpoint_not_allowed", 422)
    if not url.startswith("https://"):
        raise GmailConnectorError("google_https_required", 422)
    headers = {"Accept": "application/json"}
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    payload = urlencode(form).encode("ascii") if form is not None else None
    req = Request(url, data=payload, headers=headers,
                  method="POST" if payload is not None else "GET")
    try:
        with build_opener(_NoRedirect()).open(req, timeout=10) as response:
            if response.status != 200:
                raise GmailConnectorError("google_provider_request_failed")
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise GmailConnectorError("google_response_too_large")
            doc = json.loads(body)
            if type(doc) is not dict:
                raise ValueError("not a dictionary")
            return doc
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeError):
        raise GmailConnectorError("google_provider_request_failed") from None


def _valid_client(client_id, client_secret=""):
    if (type(client_id) is not str or
            not 20 <= len(client_id) <= 256 or
            not client_id.endswith(".apps.googleusercontent.com") or
            any(ord(ch) < 33 or ord(ch) > 126 for ch in client_id)):
        raise GmailConnectorError("google_desktop_client_id_invalid", 422)
    if type(client_secret) is not str or len(client_secret) > 256:
        raise GmailConnectorError("google_desktop_client_secret_invalid", 422)


def _validate_grant(data):
    if type(data) is not dict or set(data) != {
            "version", "provider", "scope", "client_id",
            "client_secret", "refresh_token"}:
        raise GmailConnectorError("google_grant_invalid_or_locked")
    _valid_client(data["client_id"], data["client_secret"])
    if (data["version"] != 1 or data["provider"] != "google_gmail"
            or data["scope"] != GMAIL_SCOPE
            or type(data["refresh_token"]) is not str
            or not 16 <= len(data["refresh_token"]) <= 4096):
        raise GmailConnectorError("google_grant_invalid_or_locked")
    return data


class GoogleGmailMetadata:
    def __init__(self, filename, master_key, transport=None):
        decode_master_key(master_key)  # Require the same external key as the encrypted vault.
        self.path = Path(filename).expanduser()
        self.master_key = master_key
        self.transport = transport or _official_json

    def _vault(self):
        # Never auto-create a blank Google grant during reads.
        if not self.path.is_file() or self.path.is_symlink():
            raise GmailConnectorError("google_gmail_not_connected", 409)
        try:
            return EncryptedConnectorVault(self.path, self.master_key, aad=GMAIL_AAD)
        except VaultError:
            raise GmailConnectorError("google_grant_invalid_or_locked") from None

    def _grant(self):
        try:
            return _validate_grant(self._vault().read())
        except VaultError:
            raise GmailConnectorError("google_grant_invalid_or_locked") from None

    def status(self):
        if not self.path.exists():
            return {"enabled": True, "connected": False, "provider": "google_gmail",
                    "scope": GMAIL_SCOPE, "read_only": True,
                    "model_access": False, "scheduled_access": False}
        self._grant()
        return {"enabled": True, "connected": True, "provider": "google_gmail",
                "scope": GMAIL_SCOPE, "read_only": True,
                "model_access": False, "scheduled_access": False}

    def save_grant(self, client_id, client_secret, refresh_token, *, replace=False):
        _valid_client(client_id, client_secret)
        if (type(refresh_token) is not str
                or not 16 <= len(refresh_token) <= 4096
                or any(ord(ch) < 33 or ord(ch) > 126 for ch in refresh_token)):
            raise GmailConnectorError("google_refresh_grant_invalid", 422)
        if self.path.exists() and not replace:
            raise GmailConnectorError("google_grant_exists_use_explicit_replace", 409)
        if self.path.exists():
            self._grant()  # Wrong key/corrupted grant must never be overwritten.
        data = {"version": 1, "provider": "google_gmail", "scope": GMAIL_SCOPE,
                "client_id": client_id, "client_secret": client_secret,
                "refresh_token": refresh_token}
        try:
            EncryptedConnectorVault(self.path, self.master_key, aad=GMAIL_AAD).write(data)
        except VaultError:
            raise GmailConnectorError("google_grant_storage_failed") from None

    def _access_token(self):
        grant = self._grant()
        form = {"client_id": grant["client_id"],
                "refresh_token": grant["refresh_token"],
                "grant_type": "refresh_token"}
        if grant["client_secret"]:
            form["client_secret"] = grant["client_secret"]
        try:
            token = self.transport(TOKEN_ENDPOINT, form, None)
        except Exception:
            raise GmailConnectorError("google_token_refresh_failed") from None
        access = token.get("access_token") if type(token) is dict else None
        if type(access) is not str or not 16 <= len(access) <= 8192:
            raise GmailConnectorError("google_token_refresh_failed")
        scope = token.get("scope")
        if scope is not None and GMAIL_SCOPE not in str(scope).split():
            raise GmailConnectorError("google_scope_not_granted", 403)
        return access

    def _get(self, url, access):
        try:
            result = self.transport(url, None, access)
        except Exception:
            raise GmailConnectorError("google_gmail_request_failed") from None
        if type(result) is not dict:
            raise GmailConnectorError("google_gmail_response_invalid")
        return result

    def profile(self):
        doc = self._get(GMAIL_BASE + "/profile", self._access_token())
        email = doc.get("emailAddress")
        if type(email) is not str or not 3 <= len(email) <= 320:
            raise GmailConnectorError("google_gmail_response_invalid")
        return {"email": email,
                "messages_total": _bounded_int(doc.get("messagesTotal")),
                "threads_total": _bounded_int(doc.get("threadsTotal")),
                "read_only": True, "source": "google_gmail_api"}

    def inbox(self):
        access = self._access_token()
        doc = self._get(GMAIL_BASE + "/messages?maxResults=5&labelIds=INBOX", access)
        rows = doc.get("messages", [])
        if type(rows) is not list or len(rows) > 5:
            raise GmailConnectorError("google_gmail_response_invalid")
        result = []
        for item in rows:
            if type(item) is not dict:
                raise GmailConnectorError("google_gmail_response_invalid")
            message_id = item.get("id")
            if (type(message_id) is not str or
                    not 1 <= len(message_id) <= 128 or
                    not all(ch.isalnum() or ch in "_-" for ch in message_id)):
                raise GmailConnectorError("google_gmail_response_invalid")
            detail = self._get(
                GMAIL_BASE + "/messages/" + message_id +
                "?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date",
                access)
            headers = detail.get("payload", {}).get("headers", [])
            if type(headers) is not list or len(headers) > 100:
                raise GmailConnectorError("google_gmail_response_invalid")
            fields = {}
            for h in headers:
                if type(h) is dict and type(h.get("name")) is str and type(h.get("value")) is str:
                    key = h["name"].lower()
                    if key in ("from", "subject", "date"):
                        fields[key] = h["value"][:300]
            labels = detail.get("labelIds", [])
            result.append({"id": message_id, "from": fields.get("from", ""),
                           "subject": fields.get("subject", ""),
                           "date": fields.get("date", ""),
                           "unread": type(labels) is list and "UNREAD" in labels})
        return {"messages": result, "count": len(result),
                "read_only": True, "source": "google_gmail_api"}


def _bounded_int(value):
    return value if type(value) is int and 0 <= value <= 10**9 else None
