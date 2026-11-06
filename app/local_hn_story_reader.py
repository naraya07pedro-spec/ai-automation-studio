"""One fixed read-only Hacker News API connector with per-story owner grants.

Never follows remote redirects, accepts arbitrary hosts/URLs, sends secrets or
executes provider-supplied content. Only a story/job and up to two direct
comment items are read using official HTTPS /v0/item/{id}.json endpoints.
"""
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import http.client
import json
import ssl
import unicodedata

from local_hn_story_policy import valid_story_id

HOST = "hacker-news.firebaseio.com"
MAX_RESPONSE_BYTES = 16_384
MAX_COMMENTS = 2
MAX_HTML_TEXT = 650
TIMEOUT_SECONDS = 6


class HNStoryReadError(ValueError):
    pass


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.blocked += 1
        if tag in ("p", "br") and not self.blocked:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.blocked:
            self.blocked -= 1
        if tag in ("p", "br") and not self.blocked:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(data)


def _plain(value, limit):
    if type(value) is not str:
        return ""
    try:
        parser = _PlainText()
        parser.feed(value[:max(limit * 4, 500)])
        parser.close()
        text = "".join(parser.parts)
    except (ValueError, AssertionError):
        return ""
    return " ".join("".join(" " if unicodedata.category(c).startswith("C")
                            else c for c in text).split())[:limit]


def _utc(value):
    if type(value) is not int or not 0 < value <= 4102444800:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat(
            timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return None


def _public_hn_item(item_id):
    """Hardcoded origin and path; TLS validates host; no redirect accepted."""
    if not valid_story_id(item_id):
        raise HNStoryReadError("invalid_hn_story_id")
    conn = http.client.HTTPSConnection(HOST, timeout=TIMEOUT_SECONDS,
                                      context=ssl.create_default_context())
    try:
        conn.request("GET", "/v0/item/" + str(item_id) + ".json",
                     headers={"Accept":"application/json",
                              "User-Agent":"AI-Automation-Studio-HN-ReadOnly/1"})
        response = conn.getresponse()
        if response.status != 200:
            raise HNStoryReadError("hn_provider_unavailable")
        if not response.getheader("Content-Type","").lower().startswith(
                "application/json"):
            raise HNStoryReadError("hn_provider_shape_invalid")
        raw = response.read(MAX_RESPONSE_BYTES+1)
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            raise HNStoryReadError("hn_provider_size_rejected")
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise HNStoryReadError("hn_provider_shape_invalid") from None
    except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException):
        raise HNStoryReadError("hn_provider_unavailable") from None
    finally:
        conn.close()


class AllowedHNStoryReader:
    def __init__(self, policy, fetcher=None):
        if policy is None:
            raise HNStoryReadError("hn_policy_required")
        self.policy = policy
        self.fetcher = fetcher if fetcher is not None else _public_hn_item

    def version(self):
        return self.policy.version()

    def check(self, story_id, epoch=None):
        if not valid_story_id(story_id):
            raise HNStoryReadError("invalid_hn_story_id")
        if not self.policy.is_allowed(story_id, epoch):
            raise HNStoryReadError("hn_story_not_allowed")
        return story_id

    def read(self, story_id, approved_epoch):
        if type(approved_epoch) is not int or approved_epoch < 0:
            raise HNStoryReadError("hn_approval_missing")
        # An owner revoke cannot race any provider GET in this already-started
        # step. The grant is rechecked at every request and before publishing.
        with self.policy._lock:
            self.check(story_id, approved_epoch)
            try:
                story = self.fetcher(story_id)
                if (type(story) is not dict or story.get("id") != story_id
                        or type(story.get("id")) is not int
                        or story.get("type") not in ("story","job")
                        or story.get("deleted") is True or story.get("dead") is True):
                    raise HNStoryReadError("hn_provider_shape_invalid")
                kids = story.get("kids",[])
                if (type(kids) is not list or len(kids) > 100_000
                        or any(not valid_story_id(v) for v in kids[:MAX_COMMENTS])):
                    raise HNStoryReadError("hn_provider_shape_invalid")
                comments=[]
                for kid in kids[:MAX_COMMENTS]:
                    self.check(story_id, approved_epoch)
                    entry=self.fetcher(kid)
                    if (type(entry) is not dict or type(entry.get("id")) is not int
                            or entry["id"] != kid or entry.get("type") != "comment"
                            or type(entry.get("parent")) is not int
                            or entry["parent"] != story_id):
                        raise HNStoryReadError("hn_provider_shape_invalid")
                    if entry.get("deleted") is True or entry.get("dead") is True:
                        continue
                    comments.append({"item_id":kid,
                                     "text":_plain(entry.get("text"), MAX_HTML_TEXT),
                                     "created_utc":_utc(entry.get("time"))})
                self.check(story_id, approved_epoch)
            except HNStoryReadError:
                raise
            except Exception:
                raise HNStoryReadError("hn_provider_unavailable") from None
            source = {
                "source":"HACKER_NEWS_PUBLIC_ITEM_V0",
                "provider":"Hacker News",
                "story_id":story_id,
                "story_type":story["type"],
                "title":_plain(story.get("title"), 160),
                "text_excerpt":_plain(story.get("text"), MAX_HTML_TEXT),
                "created_utc":_utc(story.get("time")),
                "story_url":"https://news.ycombinator.com/item?id="+str(story_id),
                "comments":comments,
                "comments_sampled":len(kids[:MAX_COMMENTS]),
                "comments_may_be_missing":len(kids) > MAX_COMMENTS,
                "grant_epoch":approved_epoch,
                "external_read_count":1+len(kids[:MAX_COMMENTS]),
                "external_write_count":0,
                "model_calls":0,
                "limitations":"Unverified public user-generated content; not proof of job availability, employer legitimacy, geography or employment eligibility.",
            }
            canonical=json.dumps(source,ensure_ascii=False,sort_keys=True,
                                 separators=(",",":")).encode("utf-8")
            source["source_sha256"]=sha256(canonical).hexdigest()
            source["fetched_at_utc"]=datetime.now(timezone.utc).isoformat(
                timespec="seconds")
            return json.dumps(source,ensure_ascii=False,separators=(",",":"))
