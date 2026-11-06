"""Frozen, code-owned registry of bounded local plan tools and owner-only actions.

This is NOT a dynamic plugin loader. Registry entries never execute code from
the model, request body, CSV contents or remote sources.
"""
from types import MappingProxyType


_TOOL_ROWS = (
    ("calculate", "READ_ONLY", "always", True, "Deterministic bounded arithmetic"),
    ("utc_now", "READ_ONLY", "always", True, "Current UTC timestamp"),
    ("github_public_issues", "PUBLIC_READ", "github", True, "Allowlisted public GitHub issue metadata"),
    ("github_issue_triage", "PUBLIC_READ", "github", True, "Deterministic label-based issue triage"),
    ("github_issue_analysis", "PUBLIC_READ", "github", True, "Unverified model analysis of allowed issue metadata"),
    ("local_document_question", "SELECTED_SNAPSHOT_READ", "document", True, "Question against selected document"),
    ("local_multi_document_question", "SELECTED_SNAPSHOT_READ", "documents", True, "Question against selected document set"),
    ("hn_public_story", "PUBLIC_READ", "hn", True,
     "Grant-bound public Hacker News story/job and two direct comments"),
    ("hn_jobstories_preview", "PUBLIC_READ", "hn_discovery", False,
     "Owner-clicked preview of latest 5 public HN job titles; no automatic grants"),
    ("greenhouse_job_evidence", "PUBLIC_READ", "greenhouse", False,
     "Owner-clicked exact public ATS job lookup, never implicit hiring eligibility"),
    ("local_csv_profile", "SELECTED_SNAPSHOT_READ", "csv", True, "Deterministic profile of selected CSV"),
    ("local_markdown_report_export", "LOCAL_CREATE_ONLY", "artifact", False,
     "Separately confirmed existing Markdown report export from completed plan"),
    ("local_csv_profile_json_export", "LOCAL_CREATE_ONLY", "action", False,
     "Separately confirmed, create-only JSON aggregate export from completed plan"),
)
_TOOL_MAP = MappingProxyType({r[0]: r for r in _TOOL_ROWS})
BATCH_READ_TOOLS = frozenset(r[0] for r in _TOOL_ROWS if r[3])
ACTION_TOOL = "local_csv_profile_json_export"
MARKDOWN_ACTION_TOOL = "local_markdown_report_export"
OWNER_ACTION_TOOLS = frozenset((ACTION_TOOL, MARKDOWN_ACTION_TOOL))
HN_DISCOVERY_PREVIEW = "hn_jobstories_preview"
GREENHOUSE_EVIDENCE = "greenhouse_job_evidence"
EXPLICIT_CLICK_READS = frozenset((HN_DISCOVERY_PREVIEW, GREENHOUSE_EVIDENCE))


class ToolPermissionError(ValueError):
    pass


def require_owner_action(name, *, enabled):
    """No model/tool-plan path can authorize a local write."""
    if (type(name) is not str or name not in OWNER_ACTION_TOOLS
            or not enabled or name in BATCH_READ_TOOLS
            or _TOOL_MAP[name][1] != "LOCAL_CREATE_ONLY"):
        raise ToolPermissionError("local_tool_permission_denied")
    return name


def require_discovery_preview(name, *, enabled):
    """Only the fixed owner-click UI public read effects have this grant."""
    if (type(name) is not str or name not in EXPLICIT_CLICK_READS
            or not enabled or name in BATCH_READ_TOOLS
            or _TOOL_MAP[name][1] != "PUBLIC_READ"):
        raise ToolPermissionError("hn_discovery_permission_denied")
    return name


def registered_tools(*, planning, github=False, document=False, documents=False,
                     csv=False, hn=False, hn_discovery=False,
                     greenhouse=False, artifact=False, action=False):
    """One stable capability map with explicit effects and availability."""
    conditions = {
        "always": bool(planning),
        "github": bool(planning and github),
        "document": bool(planning and document),
        "documents": bool(planning and documents),
        "csv": bool(planning and csv),
        "hn": bool(planning and hn),
        "hn_discovery": bool(planning and hn and hn_discovery),
        "greenhouse": bool(planning and greenhouse),
        "artifact": bool(planning and artifact),
        "action": bool(planning and csv and artifact and action),
    }
    return [{
        "name": name,
        "effect": effect,
        "enabled": conditions[flag],
        "batch_allowed": batch,
        "owner_confirmation_required": (
            name in OWNER_ACTION_TOOLS or name in EXPLICIT_CLICK_READS),
        "model_plannable": (
            name not in OWNER_ACTION_TOOLS and name not in EXPLICIT_CLICK_READS),
        "scope": description,
    } for name, effect, flag, batch, description in _TOOL_ROWS]
