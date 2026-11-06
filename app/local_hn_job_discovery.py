"""Explicit-click preview of public HN jobstories. No story grants or plan runs.

Unlike the grant-bound story reader, this UI-only discovery adapter obtains
just five public job titles from HN's official jobstories feed. The result is
never inserted into a model prompt, saved to the agent plan SQLite, or granted.
"""
from datetime import datetime, timezone
from hashlib import sha256
import http.client
import json
import ssl

from local_hn_story_policy import valid_story_id
from local_hn_story_reader import (
    HNStoryReadError, HOST, TIMEOUT_SECONDS, _plain, _utc,
    _public_hn_item)

LIST_PATH = "/v0/jobstories.json"
LIST_MAX_BYTES = 8192
LIST_MAX_IDS = 200
PREVIEW_MAX_ITEMS = 5
MAX_TITLE = 160


class HNDiscoveryError(ValueError):
    pass


def _fetch_job_ids():
    """One HTTPS GET, exact official origin/path, with strict response bound."""
    conn = http.client.HTTPSConnection(
        HOST, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
    try:
        conn.request("GET", LIST_PATH, headers={
            "Accept": "application/json",
            "User-Agent": "AI-Automation-Studio-HN-ReadOnly/1",
        })
        response = conn.getresponse()
        if response.status != 200:
            raise HNDiscoveryError("hn_discovery_provider_unavailable")
        if not response.getheader("Content-Type", "").lower().startswith(
                "application/json"):
            raise HNDiscoveryError("hn_discovery_provider_invalid")
        raw = response.read(LIST_MAX_BYTES + 1)
        if not raw or len(raw) > LIST_MAX_BYTES:
            raise HNDiscoveryError("hn_discovery_provider_invalid")
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise HNDiscoveryError("hn_discovery_provider_invalid") from None
    except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException):
        raise HNDiscoveryError("hn_discovery_provider_unavailable") from None
    finally:
        conn.close()


class HNJobDiscovery:
    """Read up to 5 job metadata items only when owner explicitly requests it."""

    def __init__(self, list_fetcher=None, item_fetcher=None):
        self._list_fetcher = list_fetcher or _fetch_job_ids
        self._item_fetcher = item_fetcher or _public_hn_item

    def preview(self):
        try:
            ids = self._list_fetcher()
        except HNDiscoveryError:
            raise
        except Exception:
            raise HNDiscoveryError("hn_discovery_provider_unavailable") from None
        if (type(ids) is not list or not 1 <= len(ids) <= LIST_MAX_IDS
                or any(not valid_story_id(v) for v in ids)
                or len(set(ids)) != len(ids)):
            raise HNDiscoveryError("hn_discovery_provider_invalid")

        chosen = ids[:PREVIEW_MAX_ITEMS]
        rows = []
        for job_id in chosen:
            try:
                item = self._item_fetcher(job_id)
            except HNStoryReadError as exc:
                raise HNDiscoveryError("hn_discovery_item_unavailable") from exc
            except Exception:
                raise HNDiscoveryError("hn_discovery_item_unavailable") from None
            if (type(item) is not dict
                    or type(item.get("id")) is not int
                    or item["id"] != job_id
                    or item.get("type") != "job"
                    or type(item.get("title")) is not str):
                raise HNDiscoveryError("hn_discovery_provider_invalid")
            if item.get("deleted") is True or item.get("dead") is True:
                continue
            title = _plain(item["title"], MAX_TITLE)
            if not title:
                continue
            rows.append({
                "story_id": job_id,
                "title": title,
                "created_utc": _utc(item.get("time")),
                "story_url": "https://news.ycombinator.com/item?id=" + str(job_id),
                "source_type": "UNVERIFIED_PUBLIC_HN_JOB_POST",
                "owner_story_grant": False,
            })

        # The source fingerprint explicitly excludes volatile fetched_at,
        # but includes the entire returned preview, list count and ordering.
        source = {
            "source": "HACKER_NEWS_PUBLIC_JOBSTORIES_PREVIEW_V1",
            "provider": "Hacker News public v0 API",
            "feed_url": "https://hacker-news.firebaseio.com" + LIST_PATH,
            "feed_ids_returned": len(ids),
            "ids_considered": len(chosen),
            "preview_limit": PREVIEW_MAX_ITEMS,
            "jobs": rows,
            "external_read_count": 1 + len(chosen),
            "external_write_count": 0,
            "local_file_write_count": 0,
            "model_calls": 0,
            "auto_grant_count": 0,
            "requires_separate_owner_grant": True,
            "job_active_verified": False,
            "indonesia_eligibility_verified": False,
            "applicant_data_included": False,
        }
        canonical = json.dumps(
            source, sort_keys=True, ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")
        source["source_sha256"] = sha256(canonical).hexdigest()
        source["fetched_at_utc"] = datetime.now(
            timezone.utc).isoformat(timespec="seconds")
        return source
