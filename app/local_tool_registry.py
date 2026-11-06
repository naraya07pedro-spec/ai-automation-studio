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
    ("local_csv_profile", "SELECTED_SNAPSHOT_READ", "csv", True, "Deterministic profile of selected CSV"),
    ("local_csv_profile_json_export", "LOCAL_CREATE_ONLY", "action", False,
     "Separately confirmed, create-only JSON aggregate export from completed plan"),
)
_TOOL_MAP = MappingProxyType({r[0]: r for r in _TOOL_ROWS})
BATCH_READ_TOOLS = frozenset(r[0] for r in _TOOL_ROWS if r[3])
ACTION_TOOL = "local_csv_profile_json_export"


class ToolPermissionError(ValueError):
    pass


def require_owner_action(name, *, enabled):
    """No model/tool-plan path can authorize a local write."""
    if (type(name) is not str or name != ACTION_TOOL
            or not enabled or ACTION_TOOL in BATCH_READ_TOOLS
            or _TOOL_MAP[ACTION_TOOL][1] != "LOCAL_CREATE_ONLY"):
        raise ToolPermissionError("local_tool_permission_denied")
    return ACTION_TOOL


def registered_tools(*, planning, github=False, document=False, documents=False,
                     csv=False, action=False):
    """One stable capability map with explicit effects and availability."""
    conditions = {
        "always": bool(planning),
        "github": bool(planning and github),
        "document": bool(planning and document),
        "documents": bool(planning and documents),
        "csv": bool(planning and csv),
        "action": bool(planning and csv and action),
    }
    return [{
        "name": name,
        "effect": effect,
        "enabled": conditions[flag],
        "batch_allowed": batch,
        "owner_confirmation_required": name == ACTION_TOOL,
        "model_plannable": name != ACTION_TOOL,
        "scope": description,
    } for name, effect, flag, batch, description in _TOOL_ROWS]
