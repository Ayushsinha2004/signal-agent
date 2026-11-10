"""Typed contracts between pipeline stages (PLAN.md §3.3)."""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class State(StrEnum):
    NEW = "NEW"
    NORMALISED = "NORMALISED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    QUALIFIED = "QUALIFIED"
    RESEARCHED = "RESEARCHED"
    MATCHED = "MATCHED"
    DRAFTED = "DRAFTED"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    SENT = "SENT"
    REPLIED = "REPLIED"
    INTERVIEW = "INTERVIEW"
    DEAD = "DEAD"
    FAILED = "FAILED"


# Allowed transitions; anything else is a bug (tested).
TRANSITIONS: dict[State, set[State]] = {
    State.NEW: {State.NORMALISED, State.REJECTED, State.FAILED},
    State.NORMALISED: {State.REJECTED, State.QUALIFIED, State.DUPLICATE, State.REVIEW, State.FAILED},
    State.QUALIFIED: {State.RESEARCHED, State.FAILED},
    State.RESEARCHED: {State.MATCHED, State.FAILED},
    State.MATCHED: {State.DRAFTED, State.REVIEW, State.FAILED},
    State.DRAFTED: {State.REVIEW, State.FAILED},
    State.REVIEW: {State.APPROVED, State.DEAD, State.QUALIFIED},
    State.APPROVED: {State.SENT, State.DEAD},
    State.SENT: {State.REPLIED, State.DEAD},
    State.REPLIED: {State.INTERVIEW, State.DEAD},
    State.FAILED: {State.NEW},
    State.REJECTED: {State.REVIEW},
    State.DUPLICATE: set(),
    State.INTERVIEW: set(),
    State.DEAD: set(),
}


class Contact(BaseModel):
    emails: list[str] = Field(default_factory=list, description="Emails written in the post itself. Never guess.")
    apply_urls: list[str] = Field(default_factory=list)


class PostFacts(BaseModel):
    company: str
    company_domain: str | None = Field(None, description="Domain if a URL for the company appears in the post")
    roles: list[str] = Field(description="Role titles being hired for")
    remote: Literal["yes", "no", "hybrid", "unknown"]
    location_text: str = Field(description="Location wording copied from the post, e.g. 'REMOTE (US only)'")
    countries_allowed: list[str] = Field(
        default_factory=list, description="ISO-2 codes or 'WORLDWIDE'/'EU'/'EMEA'/'APAC'/'AMERICAS' when stated"
    )
    seniority: Literal["intern", "junior", "mid", "senior", "staff_plus", "unknown"]
    years_min: int | None = None
    stack: list[str] = Field(default_factory=list)
    is_ai_role: bool = Field(description="Role involves building with LLMs, agents, ML or AI features")
    employment: list[Literal["full_time", "part_time", "contract", "freelance", "unknown"]] = Field(
        default_factory=list
    )
    salary_text: str | None = None
    contact: Contact = Field(default_factory=Contact)


class Criteria(BaseModel):
    home_country: str = "IN"
    max_years_required: int = 4
    max_age_days: int = 15
    role_keywords: list[str]
    reject_seniority: list[str] = ["staff_plus"]
    # stretch roles pass the rules but are flagged, so they get drafted and shown apart from core-fit leads
    stretch_seniority: list[str] = Field(default_factory=list)
    stretch_max_years: int | None = None
    recruiter_markers: list[str]
    blocked_location_markers: list[str]


class RuleVerdict(BaseModel):
    passed: bool
    reasons: list[str]
    flags: list[str] = Field(default_factory=list)  # soft concerns shown to the reviewer

    @property
    def stretch(self) -> bool:
        return any(f.startswith("stretch") for f in self.flags)


class Fit(BaseModel):
    role_family: Literal["ai_engineer", "fullstack_ai", "automation", "backend", "data_ml", "other"]
    is_recruiter_or_agency_repost: bool
    is_direct_hire: bool
    india_eligible: Literal["yes", "no", "unknown"]
    confidence: float = Field(ge=0, le=1)
    rationale: str


class Source(BaseModel):
    url: str
    note: str


class CompanyBrief(BaseModel):
    what_they_build: str
    stage_or_size: str | None = None
    ai_stack: list[str] = Field(default_factory=list)
    recent_signal: str | None = Field(None, description="One concrete, recent, verifiable fact worth mentioning")
    sources: list[Source] = Field(default_factory=list)
    complete: bool = True


class RequirementMatch(BaseModel):
    requirement: str
    evidence_ids: list[str]


class Match(BaseModel):
    matches: list[RequirementMatch]
    gaps: list[str]


class Claim(BaseModel):
    text: str = Field(description="One factual statement made in the message")
    evidence_id: str | None = Field(None, description="Evidence id backing a claim about the candidate")
    source_url: str | None = Field(None, description="Source URL backing a claim about the company or post")


class DraftOut(BaseModel):
    channel: Literal["email", "inmail"]
    subject: str
    body: str
    connection_note: str
    claims: list[Claim]


class JudgeScores(BaseModel):
    relevance: int = Field(ge=1, le=5)
    specificity: int = Field(ge=1, le=5)
    tone: int = Field(ge=1, le=5)
    honesty: int = Field(ge=1, le=5)
    feedback: str


class Verification(BaseModel):
    grounded_pct: float
    problems: list[str]
    judge: JudgeScores | None = None
    passed: bool


class EvidenceItem(BaseModel):
    id: str
    text: str
    metrics: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class RawPost(BaseModel):
    source: str
    external_id: str
    url: str | None
    author: str | None
    posted_at: datetime | None
    text: str
