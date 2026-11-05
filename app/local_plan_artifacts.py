"""Explicit, bounded export of a saved task report into the local owner workspace.

Nothing here runs a model or accepts an output path from the browser. Only a
COMPLETED task with an existing, valid evidence-linked report may produce a file.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading

from local_agent_plan_reports import _sources, validate_report_result, PlanReportError

MAX_ARTIFACT_BYTES = 32768
PLAN_ID = re.compile(r"[0-9a-f]{24}\Z")
_UNSAFE_MARKDOWN = re.compile(r"([\\\x60*_{}\[\]()#+.!|><&~-])")


class PlanArtifactError(Exception):
    pass


def _plain_markdown(value):
    """Preserve text as data, not active Markdown markup or remote images."""
    if type(value) is not str:
        raise PlanArtifactError("artifact_report_invalid")
    return _UNSAFE_MARKDOWN.sub(lambda match: "\\" + match.group(1), value).replace(
        "\r", " ").replace("\n", "\n> ")


def render_saved_report(plan):
    """Revalidate exact citation membership before building a local deliverable."""
    if type(plan) is not dict:
        raise PlanArtifactError("artifact_report_invalid")
    plan_id = plan.get("plan_id")
    report = plan.get("report")
    if (type(plan_id) is not str or PLAN_ID.fullmatch(plan_id) is None
            or plan.get("status") != "COMPLETED"):
        raise PlanArtifactError("artifact_requires_completed_plan")
    if report is None:
        raise PlanArtifactError("artifact_requires_saved_report")
    if (type(report) is not dict or type(report.get("findings")) is not list
            or type(report.get("summary")) is not str):
        raise PlanArtifactError("artifact_report_invalid")
    try:
        parsed = {"summary": report["summary"], "findings": [
            {"step_index": f["step_index"], "quote": f["quote"],
             "interpretation": f["interpretation"]}
            for f in report["findings"]]}
        canonical = validate_report_result(parsed, _sources(plan))
        if canonical != report:
            raise PlanArtifactError("artifact_report_invalid")
    except (PlanReportError, KeyError, ValueError, TypeError, IndexError):
        raise PlanArtifactError("artifact_report_invalid") from None

    if (type(plan.get("goal")) is not str
            or type(plan.get("model")) is not str):
        raise PlanArtifactError("artifact_report_invalid")
    lines = [
        "# Local Agent Task Deliverable",
        "",
        "Goal (owner request):",
        "> " + _plain_markdown(plan["goal"]),
        "",
        "Plan ID: `" + plan_id + "`",
        "Status: COMPLETED",
        "Model: `" + _plain_markdown(plan["model"]) + "`",
        "Evidence: exact substring membership in saved tool outputs ONLY.",
        "AI interpretations independently verified: NO",
        "",
        "## Summary (unverified AI interpretation)",
        "> " + _plain_markdown(report["summary"]),
        "",
        "## Findings",
    ]
    for idx, finding in enumerate(canonical["findings"], 1):
        lines.extend([
            "### " + str(idx) + ". Recorded step " + str(finding["step_index"]),
            "Tool: `" + finding["tool"] + "`",
            "Exact saved-output quote (escaped for Markdown display):",
            "> " + _plain_markdown(finding["quote"]),
            "AI interpretation (unverified):",
            "> " + _plain_markdown(finding["interpretation"]),
            "",
        ])
    handoffs = plan.get("handoffs") or []
    if type(handoffs) is not list:
        raise PlanArtifactError("artifact_report_invalid")
    if handoffs:
        lines.append("## Numeric handoff audit")
        for entry in handoffs:
            if type(entry) is not dict:
                raise PlanArtifactError("artifact_report_invalid")
            lines.append("- Recorded binding: `" +
                         _plain_markdown(json.dumps(
                             entry, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))) + "`")
    lines.extend([
        "", "## Source and security limitations",
        "This file is derived solely from a stored, completed read-only plan.",
        "Exact quote checks do NOT prove the AI summary or interpretations true.",
        "Source inputs and this local plaintext artifact may contain sensitive data.",
        "No cloud upload, external action, fresh tool fetch or model call occurred.",
        "",
    ])
    encoded = "\n".join(lines).encode("utf-8")
    if not 0 < len(encoded) <= MAX_ARTIFACT_BYTES:
        raise PlanArtifactError("artifact_too_large")
    return encoded


class LocalPlanArtifactExporter:
    """Write-once, deterministic filename in a server-configured directory.

    Uses an exclusive temporary file followed by a hardlink to the fixed final
    name. Never overwrite or follow an existing symlink; identical retry is a
    read-only success, changed content is an explicit conflict.
    """

    def __init__(self, directory):
        self.root = Path(directory).expanduser()
        if not self.root.is_absolute():
            raise PlanArtifactError("artifact_workspace_invalid")
        self.lock = threading.RLock()

    def _workspace(self):
        parent = self.root.parent
        if parent.is_symlink() or self.root.is_symlink():
            raise PlanArtifactError("artifact_workspace_unsafe")
        if not parent.is_dir():
            raise PlanArtifactError("artifact_workspace_unavailable")
        try:
            self.root.mkdir(mode=0o700, exist_ok=True)
        except OSError:
            raise PlanArtifactError("artifact_workspace_unavailable") from None
        if self.root.is_symlink() or not self.root.is_dir():
            raise PlanArtifactError("artifact_workspace_unsafe")
        if os.name == "posix" and (stat.S_IMODE(self.root.stat().st_mode) & 0o077):
            raise PlanArtifactError("artifact_workspace_unsafe")

    @staticmethod
    def _matching_existing(path, expected):
        # On Windows O_NOFOLLOW may be unavailable: explicitly inspect the
        # directory entry too, never accept a symlink as an identical output.
        # This supplements O_NOFOLLOW on platforms that support it.
        if path.is_symlink():
            raise PlanArtifactError("artifact_target_conflict")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(str(path), flags)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise PlanArtifactError("artifact_target_conflict")
                actual = stream.read(MAX_ARTIFACT_BYTES + 1)
            if actual != expected:
                raise PlanArtifactError("artifact_target_conflict")
            return True
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
            raise PlanArtifactError("artifact_target_conflict") from None

    def export(self, plan):
        encoded = render_saved_report(plan)
        name = "local-agent-report-" + plan["plan_id"] + ".md"
        with self.lock:
            self._workspace()
            destination = self.root / name
            created = False
            temporary = self.root / (".write-" + secrets.token_hex(16))
            fd = None
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(str(temporary), flags, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    fd = None
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # Link is exclusive and atomic on one filesystem. No
                    # replace()/rename() that could overwrite owner's files.
                    os.link(str(temporary), str(destination), follow_symlinks=False)
                    created = True
                except FileExistsError:
                    self._matching_existing(destination, encoded)
            except PlanArtifactError:
                raise
            except (OSError, ValueError):
                raise PlanArtifactError("artifact_workspace_unavailable") from None
            finally:
                if fd is not None:
                    os.close(fd)
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

            return {
                "status": "CREATED" if created else "EXISTS_IDENTICAL",
                "filename": name,
                "relative_workspace": ".ai-automation-studio/exports",
                "bytes": len(encoded),
                "sha256": sha256(encoded).hexdigest(),
                "local_file_write_count": 1 if created else 0,
                "external_write_count": 0,
                "source": "OWNER_EXPLICIT_SAVED_REPORT_EXPORT",
            }
