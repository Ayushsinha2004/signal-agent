from __future__ import annotations

import os
from typing import Any

import psycopg
import pytest

from signal_agent.config import REPO_ROOT
from signal_agent.llm.base import LLMResult, Tier, Usage
from signal_agent.models import (
    Claim,
    CompanyBrief,
    Contact,
    DraftOut,
    Fit,
    JudgeScores,
    Match,
    PostFacts,
    RequirementMatch,
    Source,
)
from signal_agent.profile import Profile, load_profile

POST_URL = "https://news.ycombinator.com/item?id=1"
POST = ("Acme AI | Founding AI Engineer | REMOTE (worldwide) | Full-time\n"
        "We build LLM agents for logistics. Python, Postgres, Claude. Email jobs@acme.ai")


def facts(**kw: Any) -> PostFacts:
    base = dict(company="Acme AI", company_domain="acme.ai", roles=["Founding AI Engineer"], remote="yes",
                location_text="REMOTE (worldwide)", countries_allowed=["WORLDWIDE"], seniority="mid",
                years_min=None, stack=["Python", "Postgres", "Claude"], is_ai_role=True,
                employment=["full_time"], contact=Contact(emails=["jobs@acme.ai"]))
    base.update(kw)
    return PostFacts(**base)


def brief() -> CompanyBrief:
    return CompanyBrief(what_they_build="LLM agents for logistics", recent_signal="hiring a founding AI engineer",
                        sources=[Source(url=POST_URL, note="the post")])


def good_draft() -> DraftOut:
    body = ("Hi, saw Acme AI is hiring a Founding AI Engineer to build LLM agents for logistics. "
            "I have built LLM agents with tool calling and Postgres chat memory in 12 production workflows, "
            "and I wrote a 50-question eval harness for a RAG assistant that answered 46 of 50 correctly. "
            "I work remotely from India and would like to talk about the role. Would a short call this week work? "
            "Example")
    return DraftOut(channel="inmail", subject="Founding AI Engineer at Acme AI", body=body,
                    connection_note="Hi, saw your Founding AI Engineer post. I build LLM agents in production.",
                    claims=[Claim(text="Acme AI builds LLM agents for logistics", source_url=POST_URL),
                            Claim(text="agents in 12 production workflows", evidence_id="ex-agents"),
                            Claim(text="46 of 50 eval", evidence_id="ex-eval")])


class FakeLLM:
    """Deterministic stand-in for the model. Returns canned objects by schema type."""

    def __init__(self, overrides: dict[type, Any] | None = None) -> None:
        self.calls: list[str] = []
        self.canned: dict[type, Any] = {
            PostFacts: facts(),
            Fit: Fit(role_family="ai_engineer", is_recruiter_or_agency_repost=False, is_direct_hire=True,
                     india_eligible="yes", confidence=0.9, rationale="says worldwide"),
            CompanyBrief: brief(),
            Match: Match(matches=[RequirementMatch(requirement="agents", evidence_ids=["ex-agents", "bogus-id"])],
                         gaps=[]),
            DraftOut: good_draft(),
            JudgeScores: JudgeScores(relevance=5, specificity=4, tone=4, honesty=5, feedback="fine"),
        }
        self.canned.update(overrides or {})

    def model_for(self, tier: Tier) -> str:
        return {"small": "claude-haiku-5-5", "large": "claude-opus-5-5", "judge": "claude-sonnet-5-5"}[tier]

    def structured(self, *, tier: Tier, system: str, user: str, schema: type, effort: str = "low",
                   max_tokens: int = 4000) -> LLMResult[Any]:
        self.calls.append(schema.__name__)
        return LLMResult(value=self.canned[schema].model_copy(deep=True),
                         usage=Usage(model=self.model_for(tier), input_tokens=1000, output_tokens=100, calls=1))

    def agent(self, *, tier: Tier, system: str, user: str, tools: list, schema: type, max_turns: int,
              effort: str = "medium") -> LLMResult[Any]:
        return self.structured(tier=tier, system=system, user=user, schema=schema)


@pytest.fixture
def example_profile() -> Profile:
    return load_profile("example", root=REPO_ROOT / "profiles")


@pytest.fixture
def db():
    url = os.environ.get("SIGNAL_TEST_DATABASE_URL",
                         "postgresql://signal:signal-local-only@127.0.0.1:55433/signal")
    try:
        admin = psycopg.connect(url, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("test Postgres not running (make up)")
    admin.execute("drop database if exists signal_test")
    admin.execute("create database signal_test")
    admin.close()
    test_url = url.rsplit("/", 1)[0] + "/signal_test"
    from signal_agent.db import connect, migrate
    migrate(test_url)
    with connect(test_url) as conn:
        yield conn
