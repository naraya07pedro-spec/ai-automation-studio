"""Conservative, deterministic public ATS job-language triage for an Indonesia-based candidate.

This is a *text-evidence index*, not an eligibility determination.
Public employer statements, including location and authorization terms, are
claims to verify. No model, network, user profile, storage or application call.
"""
import re

from local_hn_story_reader import _plain

MAX_SCAN_CHARS = 12000
MAX_QUOTE_CHARS = 190

OTHER_COUNTRIES = r"(?:United States|U\.S\.A?\.?|USA|US|Canada|United Kingdom|UK|Europe|European Union|EU|Australia|New Zealand)"
INDONESIA = r"(?:Indonesia|Indonesian)"

_LOCATION_LIMIT = (
    re.compile(r"\b(?:must|required to|need to)\s+(?:currently\s+)?(?:be\s+)?(?:based|located|reside|living|live|work(?:ing)?)\s+(?:in|within|from)\s+(?:the\s+)?"+OTHER_COUNTRIES+r"\b", re.I),
    re.compile(r"\b(?:only|exclusively)\s+(?:hiring|hire|accepting|considering)\s+(?:applicants|candidates|people|workers)?\s*(?:based|located|living|residing|in)\s+(?:in\s+)?(?:the\s+)?"+OTHER_COUNTRIES+r"\b", re.I),
    re.compile(r"\b"+OTHER_COUNTRIES+r"[\s-]+only\s+(?:remote|residents|candidates|applicants|positions|jobs|workers|roles)\b", re.I),
    re.compile(r"\b(?:remote|work\s+from\s+home)\s*[-:(]?\s*"+OTHER_COUNTRIES+r"\s+only\b", re.I),
    re.compile(r"\b(?:candidates|applicants|residents)\s+(?:must\s+be\s+)?(?:based|located|residing)\s+(?:in|within)\s+(?:the\s+)?"+OTHER_COUNTRIES+r"\b", re.I),
)
_LOCATION_INDONESIA = (
    re.compile(r"\b(?:must|required to|need to)\s+(?:currently\s+)?(?:be\s+)?(?:based|located|reside|living|live|work(?:ing)?)\s+(?:in|from)\s+"+INDONESIA+r"\b", re.I),
    re.compile(r"\b(?:hiring|hire|accepting|considering|open\s+to)\s+(?:applicants|candidates|people|workers)\s+(?:based|located|living|residing|in|from)\s+(?:in\s+)?"+INDONESIA+r"\b", re.I),
)
_WORK_AUTH = (
    re.compile(r"\b(?:must|need to|required to)\s+(?:already\s+)?(?:be\s+)?(?:legally\s+)?(?:authorized|authorised|eligible)\s+to\s+work\s+in\s+(?:the\s+)?"+OTHER_COUNTRIES+r"\b", re.I),
    re.compile(r"\b(?:visa|work\s+permit|employment)\s+sponsorship\s+(?:is\s+)?(?:not\s+(?:available|provided|offered)|unavailable)\b", re.I),
    re.compile(r"\b(?:no|without)\s+(?:visa|work\s+permit)\s+sponsorship\b", re.I),
    re.compile(r"\b"+OTHER_COUNTRIES+r"[\s-]+only\s+work\s+authori[sz]ation\s+required\b", re.I),
)
_REMOTE = re.compile(r"\b(?:remote|work\s+from\s+home|work\s+remotely|distributed\s+team)\b", re.I)
_ONSITE = re.compile(r"\b(?:on[\s-]?site|hybrid|in[\s-]?office|office[\s-]?based|not\s+remote|no\s+remote)\b", re.I)
_YEARS = re.compile(r"(?<![\d\-–])\b([1-9]|[12]\d)\s*\+?\s*(?:years|yrs)\s+(?:of\s+)?(?:relevant\s+|professional\s+|commercial\s+|hands[\s-]+on\s+)?experience\b", re.I)


def _evidence(text, pattern, field):
    match = pattern.search(text)
    if match is None:
        return None
    start = max(0, match.start() - 45)
    end = min(len(text), match.end() + 45)
    quote = text[start:end].strip()
    if start > 0:
        quote = "... " + quote
    if end < len(text):
        quote += " ..."
    return {"field": field, "quote": quote[:MAX_QUOTE_CHARS]}


def _matches(text, patterns, field):
    result = []
    for pattern in patterns:
        hit = _evidence(text, pattern, field)
        if hit and hit["quote"] not in [x["quote"] for x in result]:
            result.append(hit)
        if len(result) == 2:
            break
    return result


def assess_public_job_language(*, title, location, description):
    """Find explicit constraints in a bounded sanitized official public posting.

    "Indonesia" here means the prospective remote WORK LOCATION, not nationality
    and not a statement about the applicant's visas/foreign work authorization.
    A location exclusion is raised only for explicit restrictions in posting text.
    """
    if any(type(x) is not str for x in (title, location, description)):
        raise ValueError("invalid_public_job_triage_input")
    # _plain strips HTML scripts/styles and controls and bounds the inspected data.
    # The same result used for decisions is the result used for quotes.
    raw_len = len(description)
    body = _plain(description, MAX_SCAN_CHARS)
    loc = _plain(location, 160)
    job_title = _plain(title, 180)
    truncated = raw_len > MAX_SCAN_CHARS * 4 or len(body) >= MAX_SCAN_CHARS
    # Location labels are frequently marketing labels. Never treat their
    # mere mention of a country as an explicit exclusion.
    excluded = _matches(body, _LOCATION_LIMIT, "public_job_description")
    indonesia = _matches(body, _LOCATION_INDONESIA, "public_job_description")
    auth = _matches(body, _WORK_AUTH, "public_job_description")
    remote = _matches(body, (_REMOTE,), "public_job_description")
    onsite = _matches(body, (_ONSITE,), "public_job_description")
    years = _evidence(body, _YEARS, "public_job_description")
    if not remote and _REMOTE.search(loc+" "+job_title):
        remote = _matches(loc, (_REMOTE,), "public_location_label")
        if not remote:
            remote = _matches(job_title, (_REMOTE,), "public_job_title")
    if not onsite and _ONSITE.search(loc+" "+job_title):
        onsite = _matches(loc, (_ONSITE,), "public_location_label")
        if not onsite:
            onsite = _matches(job_title, (_ONSITE,), "public_job_title")
    if excluded and indonesia:
        country = "CONFLICTING_LOCATION_TERMS"
        verdict = "MANUAL_REVIEW_REQUIRED"
    elif excluded:
        country = "EXPLICIT_OTHER_COUNTRY_LOCATION_LIMIT"
        verdict = "POSTING_LOCATION_CONFLICT_FOR_INDONESIA"
    elif indonesia:
        country = "INDONESIA_MENTIONED_IN_LOCATION_REQUIREMENT"
        verdict = "MANUAL_REVIEW_REQUIRED"
    else:
        country = "NO_EXPLICIT_LOCATION_CONCLUSION"
        verdict = "MANUAL_REVIEW_REQUIRED"
    if remote and onsite:
        remote_status = "MIXED_OR_CONFLICTING_MODE_TERMS"
    elif onsite:
        remote_status = "ONSITE_OR_HYBRID_MENTIONED"
    elif remote:
        remote_status = "REMOTE_MENTIONED_NOT_VERIFIED"
    else:
        remote_status = "UNSPECIFIED"
    return {
        "version": "ATS_PUBLIC_ELIGIBILITY_TRIAGE_V1",
        "candidate_work_location": "Indonesia",
        "candidate_work_authorization": "NOT_ASSESSED",
        "indonesia_work_location_signal": country,
        "indonesia_candidate_verdict": verdict,
        "remote_mode_signal": remote_status,
        "foreign_work_authorization_signal": (
            "FOREIGN_AUTHORIZATION_OR_SPONSORSHIP_TERM_FOUND" if auth else "UNSPECIFIED"),
        "seniority_signal": "EXPLICIT_YEARS_MENTIONED" if years else "UNSPECIFIED",
        "seniority_years_mentioned": int(_YEARS.search(body).group(1)) if years else None,
        "evidence": {
            "location_restrictions": excluded,
            "indonesia_mentions": indonesia,
            "remote_terms": remote[:1],
            "onsite_hybrid_terms": onsite[:1],
            "foreign_work_authorization": auth,
            "experience": [years] if years else [],
        },
        "inspected_description_chars": len(body),
        "description_text_truncated": truncated,
        "source_is_public_employer_claim_not_legally_verified": True,
        "no_auto_apply_or_filter": True,
        "limitations": (
            "Pattern-based excerpts from bounded public ATS text only. "
            "May miss paraphrases, negation, later clauses or conflicting policies. "
            "Work permission, hiring geography, seniority fit, remote schedule and "
            "actual application acceptance require human/original-employer verification."
        ),
    }
