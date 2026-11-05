"""Bounded external public GitHub issue reader for explicitly approved agent plans.

Fixed api.github.com GET through existing hardened adapter. No credentials,
arbitrary URLs, issue bodies, authors, comments, or external mutations.
"""
from datetime import datetime, timezone
import json
import unicodedata

from github_public_issue_intake import (_public_issues_http, PublicGitHubFetchError,
                                        PublicGitHubReadOnlyIntake)

MAX_ISSUES = 15
MAX_TITLE = 160
MAX_LABELS = 5
MAX_LABEL = 40


class GitHubPlanReadError(Exception):
    pass


def valid_repo(repo):
    """Use the existing strict public-repo parser; no URLs or host selection."""
    return PublicGitHubReadOnlyIntake._check_repo(repo)


def _short_text(value, limit):
    if type(value) is not str:
        return ""
    return "".join(" " if unicodedata.category(char).startswith("C") else char
                   for char in value).strip()[:limit]


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

    def triage(self, repo, approved_epoch):
        """One authorized provider GET; deterministic label-based source report.

        This is a triage queue, not an AI diagnosis or true severity estimate.
        Raw titles are never interpreted as instructions or priority evidence.
        """
        record = json.loads(self.read(repo, approved_epoch))
        priority_labels = {"p0", "p1", "priority:critical", "priority:high",
                           "critical", "urgent", "security"}
        type_signals = (
            ("bug", {"bug", "type:bug", "kind:bug"}),
            ("documentation", {"docs", "documentation", "type:docs"}),
            ("feature", {"enhancement", "feature", "type:feature"}),
            ("maintenance", {"maintenance", "chore", "type:chore"}),
        )
        reviewed = []
        categories = {"bug": 0, "documentation": 0, "feature": 0,
                      "maintenance": 0, "unclassified": 0}
        flagged = 0
        for issue in record["issues"]:
            original_labels = issue["labels"]
            normalized = {label.casefold().strip() for label in original_labels}
            priority_evidence = sorted(normalized & priority_labels)
            if priority_evidence:
                flagged += 1
            category = "unclassified"
            category_evidence = []
            for name, markers in type_signals:
                matched = sorted(normalized & markers)
                if matched:
                    category = name
                    category_evidence = matched
                    break
            categories[category] += 1
            reviewed.append({
                "number": issue["number"],
                "title": issue["title"],
                "url": issue["url"],
                "labels": original_labels,
                "category": category,
                "category_evidence_labels": category_evidence,
                "priority_signal": "REVIEW_FLAGGED" if priority_evidence
                                   else "UNASSESSED",
                "priority_evidence_labels": priority_evidence,
                "reason": ("Priority flag appears in visible GitHub labels."
                           if priority_evidence else
                           "No recognized priority label; severity unknown."),
            })
        reviewed.sort(key=lambda i: (
            i["priority_signal"] != "REVIEW_FLAGGED", i["number"]))
        return json.dumps({
            "source": "GITHUB_PUBLIC_ISSUE_TRIAGE_READ_ONLY",
            "repository": record["repository"],
            "fetched_at_utc": record["fetched_at_utc"],
            "method": "DETERMINISTIC_LABEL_RULES_V1",
            "limitations": ("Only first-page public open issues, visible labels "
                            "and title. No bodies, authors, comments or verified severity."),
            "page_limit": record["page_limit"],
            "returned_count": record["returned_count"],
            "may_have_more": record["may_have_more"],
            "review_flagged_count": flagged,
            "unassessed_count": len(reviewed) - flagged,
            "category_counts": categories,
            "issues": reviewed,
            "external_read_count": 1,
            "external_write_count": 0,
        }, ensure_ascii=False, separators=(",", ":"))
