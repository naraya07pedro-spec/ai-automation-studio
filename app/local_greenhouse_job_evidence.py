"""Owner-clicked official Greenhouse Job Board evidence for ONE public job post.

No arbitrary URL, automatic source matching, authorization bypass, model
prompt, external writes, applicant questions, credentials or form submission.
One bounded GET to a fixed HTTPS host, with a deliberately narrow summary.
"""
from datetime import datetime, timezone
from hashlib import sha256
import http.client
import json
import re
import ssl

from local_hn_story_reader import _plain
from local_remote_job_eligibility import assess_public_job_language

HOST = "boards-api.greenhouse.io"
BOARD = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?\Z")
MAX_JOB_ID = 999_999_999
MAX_HN_ID = 999_999_999
MAX_BYTES = 262_144
TIMEOUT_SECONDS = 8


class GreenhouseEvidenceError(ValueError):
    pass


def validate_greenhouse_target(board, job_id, hn_story_id=None):
    if (type(board) is not str or BOARD.fullmatch(board) is None
            or board == "internal"
            or type(job_id) is not int or not 1 <= job_id <= MAX_JOB_ID
            or (hn_story_id is not None and
                (type(hn_story_id) is not int or not 1 <= hn_story_id <= MAX_HN_ID))):
        raise GreenhouseEvidenceError("invalid_greenhouse_target")
    return board, job_id, hn_story_id


def _get_public_job(board, job_id):
    """Hardcoded host/path, verified TLS, never follows redirects or POSTS."""
    validate_greenhouse_target(board, job_id)
    conn = http.client.HTTPSConnection(
        HOST, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
    path = "/v1/boards/" + board + "/jobs/" + str(job_id)
    try:
        conn.request("GET", path, headers={
            "Accept": "application/json",
            "User-Agent": "AI-Automation-Studio-Greenhouse-Evidence/1",
        })
        response = conn.getresponse()
        if response.status in (404, 410):
            raise GreenhouseEvidenceError("greenhouse_job_not_published_or_not_found")
        if response.status != 200:
            raise GreenhouseEvidenceError("greenhouse_provider_unavailable")
        if not response.getheader("Content-Type", "").lower().startswith(
                "application/json"):
            raise GreenhouseEvidenceError("greenhouse_provider_shape_invalid")
        raw = response.read(MAX_BYTES + 1)
        if not raw or len(raw) > MAX_BYTES:
            raise GreenhouseEvidenceError("greenhouse_response_size_rejected")
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise GreenhouseEvidenceError("greenhouse_provider_shape_invalid") from None
    except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException):
        raise GreenhouseEvidenceError("greenhouse_provider_unavailable") from None
    finally:
        conn.close()


def _date(value):
    if type(value) is not str or len(value) > 48:
        return None
    try:
        # Do not interpret timestamps as newly opened requisitions.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return None


class GreenhouseJobEvidence:
    def __init__(self, fetcher=None):
        self.fetcher = fetcher if fetcher is not None else _get_public_job

    def preview(self, board, job_id, hn_story_id=None):
        validate_greenhouse_target(board, job_id, hn_story_id)
        try:
            data = self.fetcher(board, job_id)
        except GreenhouseEvidenceError:
            raise
        except Exception:
            raise GreenhouseEvidenceError("greenhouse_provider_unavailable") from None
        if (type(data) is not dict or type(data.get("id")) is not int
                or data["id"] != job_id
                or type(data.get("title")) is not str
                or not data["title"].strip()
                or type(data.get("location")) is not dict):
            raise GreenhouseEvidenceError("greenhouse_provider_shape_invalid")
        title = _plain(data["title"], 180)
        if not title:
            raise GreenhouseEvidenceError("greenhouse_provider_shape_invalid")
        location = _plain(data["location"].get("name"), 160)
        company = _plain(data.get("company_name"), 140)
        details = _plain(data.get("content"), 640)
        # Greenhouse's absolute_url may be a third-party domain; never
        # follow or copy an untrusted returned URL into the UI as authority.
        # The canonical link is built from our validated board+job ID.
        item = {
            "source": "GREENHOUSE_OFFICIAL_PUBLIC_JOB_BOARD_V1",
            "ats_provider": "Greenhouse",
            "board_token": board,
            "job_id": job_id,
            "canonical_job_url": (
                "https://job-boards.greenhouse.io/" + board
                + "/jobs/" + str(job_id)),
            "api_evidence_url": (
                "https://" + HOST + "/v1/boards/"
                + board + "/jobs/" + str(job_id)),
            "title": title,
            "company_display_name": company,
            "location_label": location,
            "description_excerpt": details,
            "eligibility_triage": assess_public_job_language(
                title=data["title"],
                location=data["location"].get("name") or "",
                description=data.get("content") if type(data.get("content")) is str else ""),
            "first_published_utc": _date(data.get("first_published")),
            "updated_at_utc": _date(data.get("updated_at")),
            "application_deadline_utc": _date(data.get("application_deadline")),
            "public_job_post_observed": True,
            "posting_accessible_at_check": True,
            "accepting_applications_verified": False,
            "remote_work_verified": False,
            "indonesia_eligibility_verified": False,
            "employer_identity_independently_verified": False,
            "hn_story_id_supplied_by_owner": hn_story_id,
            "hn_to_ats_match_verified": False,
            "external_read_count": 1,
            "external_write_count": 0,
            "model_calls": 0,
            "local_file_write_count": 0,
            "applicant_data_sent": False,
        }
        canonical = json.dumps(
            item, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")
        item["source_sha256"] = sha256(canonical).hexdigest()
        item["checked_at_utc"] = datetime.now(
            timezone.utc).isoformat(timespec="seconds")
        return item
