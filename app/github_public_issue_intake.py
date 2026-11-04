"""Public GitHub issue *read-only* intake, with explicit local-only import.

The only remote host is api.github.com:443 over verified HTTPS. This code
does not use credentials, proxy redirects, arbitrary URLs, issue titles,
bodies, author identities, comments or webhook writes.

Only issue NUMBERS plus three strict workflow labels are transformed into
pseudonymous local event IDs and categorical rule input.
"""
from datetime import datetime, timezone
import hashlib
import http.client
import json
import re
import secrets
import ssl
import threading
import time

from local_workflow_core import ID_PATTERN, _error

# Owner 1..39; public repo 1..100. Do not accept an arbitrary URL or host.
_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$", re.ASCII)
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$", re.ASCII)
_ISSUE_LIMIT = 15
_MAX_BYTES = 524288
_PREVIEW_TTL_SEC = 300
_MAX_SNAPSHOTS = 12
_GITHUB_API_VERSION = "2026-03-10"
_LABELS = {
    "service_code": ("aas:service:automation",
                     "aas:service:integration", "aas:service:consulting"),
    "budget_tier": ("aas:budget:1", "aas:budget:2", "aas:budget:3"),
    "timeline": ("aas:timeline:urgent",
                 "aas:timeline:soon", "aas:timeline:flexible"),
}


class PublicGitHubFetchError(Exception):
    """A provider failure with an intentionally non-identifying code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _public_issues_http(repo):
    """Make exactly one unauthenticated GET to a fixed GitHub REST endpoint.

    No urllib opener, redirects, custom host, Authorization or user-provided
    port. Abort on unexpected status/content type/oversized response.
    """
    connection = http.client.HTTPSConnection(
        "api.github.com", port=443, timeout=5,
        context=ssl.create_default_context())
    owner, name = repo.split("/")
    path = f"/repos/{owner}/{name}/issues?state=open&per_page={_ISSUE_LIMIT}&page=1"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "AI-Automation-Studio-Local-ReadOnly",
        "X-GitHub-Api-Version": _GITHUB_API_VERSION,
    }
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status == 404:
            raise PublicGitHubFetchError("public_repo_not_found")
        if response.status in (403, 429):
            raise PublicGitHubFetchError("github_rate_limited_or_forbidden")
        if response.status in (301, 302, 303, 307, 308):
            raise PublicGitHubFetchError("github_redirect_blocked")
        if response.status != 200:
            raise PublicGitHubFetchError("github_unavailable")
        content_type = response.getheader("Content-Type", "").lower().split(";")[0].strip()
        if content_type not in ("application/json", "application/vnd.github+json"):
            raise PublicGitHubFetchError("unexpected_provider_content_type")
        raw = response.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise PublicGitHubFetchError("provider_response_too_large")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise PublicGitHubFetchError("invalid_provider_json") from None
        if type(decoded) is not list or len(decoded) > _ISSUE_LIMIT:
            raise PublicGitHubFetchError("invalid_provider_shape")
        return decoded
    except (OSError, TimeoutError):
        raise PublicGitHubFetchError("github_connection_unavailable") from None
    finally:
        connection.close()


def _sanitize_issues(repo, issues):
    """Ignore PRs, partial/ambiguous labels, and all untrusted free text."""
    if type(issues) is not list or len(issues) > _ISSUE_LIMIT:
        raise PublicGitHubFetchError("invalid_provider_shape")
    converted, seen = [], set()
    ignored = 0
    repo_hash = hashlib.sha256(repo.lower().encode("ascii")).hexdigest()[:12]
    for item in issues:
        if (type(item) is not dict or "pull_request" in item
                or item.get("state") != "open"):
            ignored += 1
            continue
        number = item.get("number")
        labels = item.get("labels")
        if (type(number) is not int or not 0 < number <= 999999999
                or type(labels) is not list or len(labels) > 100
                or number in seen):
            ignored += 1
            continue
        seen.add(number)
        names = {x.get("name") for x in labels
                 if type(x) is dict and type(x.get("name")) is str}
        values = {}
        valid = True
        for key, choices in _LABELS.items():
            matched = [name for name in choices if name in names]
            if len(matched) != 1:
                valid = False
                break
            values[key] = matched[0].rsplit(":", 1)[1]
        if not valid:
            ignored += 1
            continue
        converted.append({
            "event_id": f"gh_{repo_hash}_{number}",
            "service_code": values["service_code"],
            "budget_tier": int(values["budget_tier"]),
            "timeline": values["timeline"],
        })
    return converted, ignored


class PublicGitHubReadOnlyIntake:
    """Ephemeral 5-minute approved-source snapshots; SQLite is only local."""

    def __init__(self, core, batch, fetcher=None, clock=None, policy=None):
        self.core, self.batch = core, batch
        self.policy = policy
        self.fetcher = fetcher if fetcher is not None else _public_issues_http
        self.clock = clock if clock is not None else time.monotonic
        self._lock = threading.RLock()
        self._snapshots = {}

    @staticmethod
    def _check_repo(value):
        if type(value) is not str or len(value) > 142 or value.count("/") != 1:
            return None
        owner, name = value.split("/")
        if (not _OWNER.fullmatch(owner) or not _REPO.fullmatch(name)
                or name in (".", "..")):
            return None
        return owner + "/" + name

    def _prune(self):
        now = self.clock()
        self._snapshots = {key: value for key, value in self._snapshots.items()
                           if value["expires"] > now}
        while len(self._snapshots) >= _MAX_SNAPSHOTS:
            del self._snapshots[next(iter(self._snapshots))]

    def preview(self, request):
        if type(request) is not dict or set(request) != {"workflow_id", "repo"}:
            return _error("invalid_github_preview_fields")
        wid, repo_input = request["workflow_id"], request["repo"]
        if type(wid) is not str or not ID_PATTERN.fullmatch(wid):
            return _error("invalid_workflow_id")
        repo = self._check_repo(repo_input)
        if repo is None:
            return _error("invalid_public_repo")
        policy_epoch = self.policy.version() if self.policy is not None else None
        if self.policy is not None and not self.policy.is_allowed(repo, policy_epoch):
            return _error("public_source_not_allowed", 403)
        # Fail before any network read if local work is not ACTIVE.
        with self.core._lock:
            local = self.core.db.execute(
                "SELECT status FROM local_workflows WHERE workflow_id=?",
                (wid,)).fetchone()
        if local is None:
            return _error("workflow_not_found", 404)
        if local[0] != "ACTIVE":
            return _error("workflow_not_active", 409)
        try:
            issues = self.fetcher(repo)
            rows, ignored = _sanitize_issues(repo, issues)
        except PublicGitHubFetchError as error:
            return _error(error.code, 502)
        except Exception:
            # Do not surface provider content, URLs, secrets or raw stack.
            return _error("github_connection_unavailable", 502)
        if self.policy is not None and not self.policy.is_allowed(repo, policy_epoch):
            return _error("public_source_permission_changed", 409)
        if not rows:
            return 200, {
                "status": "NO_MATCHING_PUBLIC_ISSUES", "workflow_id": wid,
                "source": "PUBLIC_GITHUB_ISSUES_READ_ONLY", "repo": repo,
                "scanned": len(issues), "ignored_count": ignored,
                "new_count": 0, "replay_count": 0,
                "external_dispatch_count": 0,
            }
        # Convert *only* allowed categories and synthetic numeric IDs into the
        # same safe CSV format used by Slice 38. No title/body/user fields.
        csv_text = "event_id,service_code,budget_tier,timeline\n" + "".join(
            f'{v["event_id"]},{v["service_code"]},{v["budget_tier"]},{v["timeline"]}\n'
            for v in rows)
        code, assessed = self.batch.preview(
            {"workflow_id": wid, "csv_text": csv_text})
        if code != 200:
            return code, assessed
        snapshot_id = secrets.token_urlsafe(24)
        with self._lock:
            if self.policy is not None and not self.policy.is_allowed(repo, policy_epoch):
                return _error("public_source_permission_changed", 409)
            self._prune()
            self._snapshots[snapshot_id] = {
                "workflow_id": wid,
                "source_repo": repo,
                "source_policy_epoch": policy_epoch,
                "csv_text": csv_text,
                "expected_version": assessed["workflow_version"],
                "preview_token": assessed["preview_token"],
                "expires": self.clock() + _PREVIEW_TTL_SEC,
            }
        return 200, {
            "status": "PUBLIC_GITHUB_PREVIEW_READY", "workflow_id": wid,
            "source": "PUBLIC_GITHUB_ISSUES_READ_ONLY", "repo": repo,
            "scanned": len(issues), "ignored_count": ignored,
            "new_count": assessed["new_count"],
            "replay_count": assessed["replay_count"],
            "rows": assessed["rows"],
            "snapshot_id": snapshot_id,
            "snapshot_ttl_seconds": _PREVIEW_TTL_SEC,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "external_dispatch_count": 0,
        }

    def commit(self, request):
        if type(request) is not dict or set(request) != {
                "workflow_id", "snapshot_id"}:
            return _error("invalid_github_commit_fields")
        wid, snapshot_id = request["workflow_id"], request["snapshot_id"]
        if (type(wid) is not str or not ID_PATTERN.fullmatch(wid)
                or type(snapshot_id) is not str or len(snapshot_id) != 32):
            return _error("invalid_github_commit_fields")
        # Lock policy through local database commit: revocation cannot
        # happen between permission check and local persistence.
        from contextlib import nullcontext
        guard = self.policy._lock if self.policy is not None else nullcontext()
        with guard:
            with self._lock:
                self._prune()
                record = self._snapshots.get(snapshot_id)
                if record is None or record["workflow_id"] != wid:
                    return _error("github_snapshot_not_found", 409)
                if (self.policy is not None
                        and not self.policy.is_allowed(
                            record["source_repo"], record["source_policy_epoch"])):
                    del self._snapshots[snapshot_id]
                    return _error("public_source_permission_changed", 403)
                del self._snapshots[snapshot_id]
            code, outcome = self.batch.commit({
                "workflow_id": wid,
                "csv_text": record["csv_text"],
                "expected_version": record["expected_version"],
                "preview_token": record["preview_token"],
            })
            if code != 200:
                return code, outcome
            return 200, {
                **outcome,
                "status": "PUBLIC_GITHUB_IMPORT_COMMITTED_LOCAL",
                "source": "PUBLIC_GITHUB_ISSUES_READ_ONLY",
            }
