"""Deterministic filters. Pre-rules run on raw text before any LLM call (ADR-002); post-rules run on PostFacts."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from ..models import Criteria, PostFacts, RuleVerdict

OPEN_WORLD = re.compile(r"worldwide|anywhere|global(ly)?|all time ?zones|any time ?zone|\bindia\b|\bapac\b|\basia\b",
                        re.I)
OK_REGIONS = {"WORLDWIDE", "APAC", "ASIA", "IN"}
REGION_LOCKED_REMOTE = re.compile(
    r"remote\s*[(\-:,/]\s*(?:us|usa|u\.s\.?|united states|canada|us\s*/\s*canada|us\s*&\s*canada|north america|"
    r"americas|eu|europe|eea|uk|germany|latam|cet|est|pst)\b|(?:us|usa|eu|uk|canada|europe)[- ](?:only )?remote\b",
    re.I)
ONSITE = re.compile(r"\bonsite\b|\bon-site\b|\bin[- ]office\b|\bin[- ]person\b", re.I)
REMOTE = re.compile(r"\bremote\b", re.I)


def _has(text: str, markers: list[str]) -> str | None:
    low = text.lower()
    return next((m for m in markers if m in low), None)


def pre_rules(text: str, posted_at: datetime | None, c: Criteria, now: datetime | None = None) -> RuleVerdict:
    reasons: list[str] = []
    now = now or datetime.now(UTC)
    if posted_at and (now - posted_at).days > c.max_age_days:
        reasons.append(f"older than {c.max_age_days} days ({(now - posted_at).days}d)")
    head = text[:600]
    if m := _has(head, c.recruiter_markers):
        reasons.append(f"recruiter/agency marker: '{m}'")
    if (m := _has(head, c.blocked_location_markers)) and not OPEN_WORLD.search(head):
        reasons.append(f"location excludes {c.home_country}: '{m}'")
    if not OPEN_WORLD.search(head):
        if m2 := REGION_LOCKED_REMOTE.search(head):
            reasons.append(f"remote limited to a region: '{m2.group(0)}'")
        elif ONSITE.search(head) and not REMOTE.search(head):
            reasons.append("on-site only")
    if not _has(text, c.role_keywords):
        reasons.append("no target role keyword")
    return RuleVerdict(passed=not reasons, reasons=reasons)


def post_rules(f: PostFacts, c: Criteria) -> RuleVerdict:
    reasons: list[str] = []
    flags: list[str] = []
    if f.remote == "no":
        reasons.append(f"not remote: {f.location_text}")
    elif f.remote == "hybrid" and c.home_country not in f.countries_allowed:
        reasons.append(f"hybrid outside {c.home_country}: {f.location_text}")
    elif f.remote == "unknown":
        flags.append("remote policy unclear")
    allowed = {x.upper() for x in f.countries_allowed}
    if allowed and not (allowed & (OK_REGIONS | {c.home_country})):
        reasons.append(f"countries {sorted(allowed)} exclude {c.home_country}")
    elif not allowed:
        flags.append("countries not stated")
    if f.years_min is not None and f.years_min > c.max_years_required:
        if c.stretch_max_years is not None and f.years_min <= c.stretch_max_years:
            flags.append(f"stretch: asks {f.years_min}+ years")
        else:
            reasons.append(f"asks {f.years_min}+ years (max {c.stretch_max_years or c.max_years_required})")
    if f.seniority in c.reject_seniority:
        reasons.append(f"seniority {f.seniority}")
    elif f.seniority in c.stretch_seniority:
        flags.append(f"stretch: {f.seniority} role")
    if not f.is_ai_role and not _has(" ".join(f.roles + f.stack), c.role_keywords):
        reasons.append("not an AI / target role")
    if not (f.contact.emails or f.contact.apply_urls):
        flags.append("no contact channel in post")
    return RuleVerdict(passed=not reasons, reasons=reasons, flags=flags)
