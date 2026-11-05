"""Bounded external public GitHub issue reader for explicitly approved agent plans.

Fixed api.github.com GET through existing hardened adapter. No credentials,
arbitrary URLs, issue bodies, authors, comments, or external mutations.
"""
from datetime import datetime, timezone
import json
import re

from github_public_issue_intake import (_public_issues_http, PublicGitHubFetchError,
                                        PublicGitHubReadOnlyIntake)

MAX_ISSUES = 15
MAX_TITLE = 160
MAX_LABELS = 5
MAX_LABEL = 40
_SAFE_TEXT = re.compile(r"[\x00-\x1f\x7f]")


class GitHubPlanReadError(Exception):
    pass


def valid_repo(repo):
    """Use the existing strict public-repo parser; no URLs or host selection."""
    return PublicGitHubReadOnlyIntake._check_repo(repo)


def _short_text(value, limit):
    if type(value) is not str:
        return ""
    return _SAFE_TEXT.sub(" ", value).strip()[:limit]


class AllowedGitHubIssuesReader:
    def __init__(self, policy, fetcher=None):
        if policy is None:
            raise GitHubPlanReadError("public_source_policy_required")
        self.policy = policy
        self.fetcher = fetcher if fetcher is not None else _public_issues_http

    def check(self, repo, epoch=None):
        canonical = valid_repo(repo)
        if canonical is None:
            raise GitHubPlanReadError("invalid_public_repo")
        if not self.policy.is_allowed(canonical, epoch):
            raise GitHubPlanReadError("public_source_not_allowed")
        return canonical

    def version(self):
        return self.policy.version()

    def read(self, repo, approved_epoch):
        """Check policy before and after read while holding revocation lock."""
        if type(approved_epoch) is not int or approved_epoch < 0:
            raise GitHubPlanReadError("public_source_approval_missing")
        with self.policy._lock:
            canonical = self.check(repo, approved_epoch)
            try:
                data = self.fetcher(canonical)
            except PublicGitHubFetchError as exc:
                raise GitHubPlanReadError(exc.code) from None
            except Exception:
                raise GitHubPlanReadError("github_connection_unavailable") from None
            # The injected test fetcher has exactly the same bounded contract.
            if type(data) is not list or len(data) > MAX_ISSUES:
                raise GitHubPlanReadError("invalid_provider_shape")
            self.check(canonical, approved_epoch)
            items, seen = [], set()
            for entry in data:
                if type(entry) is not dict or "pull_request" in entry:
                    continue
                number = entry.get("number")
                if (entry.get("state") != "open" or type(number) is not int
                        or not 0 < number <= 999999999 or number in seen):
                    continue
                seen.add(number)
                labels = entry.get("labels")
                if type(labels) is not list or len(labels) > 100:
                    labels = []
                sanitized_labels = []
                for label in labels:
                    if type(label) is not dict:
                        continue
                    value = _short_text(label.get("name"), MAX_LABEL)
                    if value and len(sanitized_labels) < MAX_LABELS:
                        sanitized_labels.append(value)
                items.append({
                    "number": number,
                    "title": _short_text(entry.get("title"), MAX_TITLE),
                    "labels": sanitized_labels,
                    "url": "https://github.com/" + canonical + "/issues/" + str(number),
                })
            return json.dumps({
                "source": "GITHUB_PUBLIC_ISSUES_READ_ONLY",
                "repository": canonical,
                "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "page_limit": MAX_ISSUES,
                "returned_count": len(items),
                "may_have_more": len(data) == MAX_ISSUES,
                "issues": items,
                "external_read_count": 1,
                "external_write_count": 0,
            }, ensure_ascii=False, separators=(",", ":"))
