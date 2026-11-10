"""OpenRouter adapter against a mocked HTTP API: parsing, repair, tools, quota; plus the disk cache and run pausing."""
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from conftest import POST, POST_URL, FakeLLM

from signal_agent.collectors import save_posts
from signal_agent.config import Settings
from signal_agent.llm import QuotaExhausted, Tier, ToolSpec, cost_usd
from signal_agent.llm.cache import CachingLLM
from signal_agent.llm.factory import make_llm, provider, resolved
from signal_agent.llm.openrouter_llm import OpenRouterLLM, extract_json, inline_refs
from signal_agent.models import CompanyBrief, Fit, PostFacts, RawPost, State
from signal_agent.pipeline.runner import create_leads, process, sync_profile

FIT = {"role_family": "ai_engineer", "is_recruiter_or_agency_repost": False, "is_direct_hire": True,
       "india_eligible": "yes", "confidence": 0.8, "rationale": "worldwide"}


def settings(**kw: Any) -> Settings:
    base = dict(openrouter_api_key="sk-or-test", anthropic_api_key=None, signal_llm_provider="openrouter",
                signal_llm_cache=False)
    base.update(kw)
    return resolved(Settings(**base))


def reply(content: str = "", tool_calls: list | None = None, model: str = "m:free") -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"model": model, "choices": [{"message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def llm_with(responses: list, **kw: Any) -> tuple[OpenRouterLLM, list[dict]]:
    sent: list[dict] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        r = queue.pop(0)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)

    client = httpx.Client(base_url="https://openrouter.test", transport=httpx.MockTransport(handler))
    return OpenRouterLLM(settings(**kw), client=client, min_interval_s=0, busy_wait_s=0), sent


def test_helpers():
    assert extract_json('<think>hmm</think>Sure!\n```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('noise {"a": {"b": 2}} trailing') == '{"a": {"b": 2}}'
    schema = inline_refs(PostFacts.model_json_schema())
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    assert cost_usd("nvidia/nemotron-3.5-super-vl:free", 10**6, 10**6) == 0.0


def test_factory_defaults():
    s = settings()
    assert provider(s) == "openrouter"
    assert s.signal_model_small.endswith(":free") and s.signal_model_judge != s.signal_model_large
    assert provider(Settings(anthropic_api_key="x", signal_llm_provider="auto")) == "anthropic"
    assert isinstance(make_llm(settings(signal_llm_cache=True)), CachingLLM)


def test_structured_parses_and_sends_schema():
    llm, sent = llm_with([reply("```json\n" + json.dumps(FIT) + "\n```")])
    res = llm.structured(tier=Tier.SMALL, system="classify", user="post", schema=Fit)
    assert res.value.india_eligible == "yes" and res.usage.input_tokens == 100 and res.usage.calls == 1
    body = sent[0]
    assert body["model"] == "nvidia/nemotron-3.5-super-vl:free"
    assert body["response_format"]["type"] == "json_schema"  # this model supports structured outputs
    assert "india_eligible" in body["messages"][0]["content"]  # schema also in the prompt for every model
    assert body["models"][0] == body["model"] and len(body["models"]) == 3  # fallback routing for busy providers


def test_judge_fallbacks_never_include_the_drafter():
    llm, _ = llm_with([])
    assert llm.model_for(Tier.LARGE) not in [llm.model_for(Tier.JUDGE), *llm.fallbacks["judge"]]


def test_structured_repairs_once_then_gives_up():
    bad = dict(FIT, india_eligible="maybe")
    llm, sent = llm_with([reply(json.dumps(bad)), reply(json.dumps(FIT))])
    assert llm.structured(tier=Tier.SMALL, system="s", user="u", schema=Fit).usage.calls == 2
    assert "did not validate" in sent[1]["messages"][-1]["content"]
    llm, _ = llm_with([reply("not json"), reply("still not")])
    with pytest.raises(ValueError, match="no valid Fit"):
        llm.structured(tier=Tier.SMALL, system="s", user="u", schema=Fit)


def test_agent_runs_tools_within_budget():
    calls = [{"id": "c1", "type": "function", "function": {"name": "fetch_url", "arguments": '{"url": "https://a.ai"}'}}]
    brief = {"what_they_build": "agents", "sources": [{"url": "https://a.ai", "note": "home"}]}
    fetched: list[str] = []
    tool = ToolSpec(name="fetch_url", description="fetch", input_schema={"type": "object"},
                    fn=lambda a: fetched.append(a["url"]) or "Acme builds agents")
    llm, sent = llm_with([reply(tool_calls=calls), reply(tool_calls=calls), reply(json.dumps(brief))])
    res = llm.agent(tier=Tier.SMALL, system="research", user="Acme", tools=[tool], schema=CompanyBrief, max_turns=2)
    assert fetched == ["https://a.ai", "https://a.ai"]  # stopped at max_turns, then one final structured call
    assert len(sent) == 3 and sent[1]["messages"][-1]["role"] == "tool"
    assert res.value.what_they_build == "agents" and len(res.transcript) == 2


def test_daily_cap_raises_quota_exhausted():
    far = str(int((datetime.now(UTC).timestamp() + 6 * 3600) * 1000))
    llm, _ = llm_with([httpx.Response(429, json={"error": {"message": "free-models-per-day"}},
                                      headers={"x-ratelimit-reset": far})])
    with pytest.raises(QuotaExhausted, match="resets in"):
        llm.structured(tier=Tier.SMALL, system="s", user="u", schema=Fit)
    llm, _ = llm_with([], signal_max_requests=0)
    with pytest.raises(QuotaExhausted, match="local request budget"):
        llm.structured(tier=Tier.SMALL, system="s", user="u", schema=Fit)


def test_error_inside_200_is_raised():
    llm, _ = llm_with([{"error": {"code": 400, "message": "bad schema"}}])
    with pytest.raises(RuntimeError, match="bad schema"):
        llm.structured(tier=Tier.SMALL, system="s", user="u", schema=Fit)


def test_cache_hits_are_free(tmp_path):
    inner = FakeLLM()
    llm = CachingLLM(inner, root=tmp_path)
    a = llm.structured(tier=Tier.SMALL, system="s", user="u", schema=Fit)
    b = llm.structured(tier=Tier.SMALL, system="s", user="u", schema=Fit)
    assert inner.calls == ["Fit"] and (llm.hits, llm.misses) == (1, 1)
    assert a.value == b.value and a.usage.calls == 1 and b.usage.calls == 0
    llm.structured(tier=Tier.SMALL, system="s", user="other post", schema=Fit)
    assert llm.misses == 2


@pytest.mark.db
def test_quota_pauses_lead_instead_of_failing(db, example_profile):
    class Capped(FakeLLM):
        def agent(self, **kw):
            raise QuotaExhausted("daily cap")
    sync_profile(db, example_profile)
    save_posts(db, [RawPost(source="hn", external_id="1", url=POST_URL, author="f", posted_at=datetime.now(UTC),
                            text=POST)])
    create_leads(db, example_profile.id)
    lead_id = db.execute("select id from leads").fetchone()["id"]
    with pytest.raises(QuotaExhausted):
        process(db, Capped(), example_profile, lead_id)
    assert db.execute("select state from leads where id = %s", (lead_id,)).fetchone()["state"] == "QUALIFIED"
    assert db.execute("select status from step_runs where stage = 'research'").fetchone()["status"] == "quota"
    assert process(db, FakeLLM(), example_profile, lead_id) is State.REVIEW  # resumes from where it paused


def test_upstream_busy_fails_the_call_not_the_run():
    from signal_agent.llm import ProviderBusy

    busy = httpx.Response(429, json={"error": {"message": "x is temporarily rate-limited upstream"}})
    llm, sent = llm_with([busy, busy])
    with pytest.raises(ProviderBusy):
        llm.structured(tier=Tier.JUDGE, system="s", user="u", schema=Fit)
    assert len(sent) == 2  # one retry, then give up on this call
    assert not issubclass(ProviderBusy, QuotaExhausted)


def test_cut_off_reply_retries_with_more_room():
    cut = {"model": "m", "choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {}}
    llm, sent = llm_with([cut, reply(json.dumps(FIT))])
    assert llm.structured(tier=Tier.SMALL, system="s", user="u", schema=Fit).value.confidence == 0.8
    assert sent[0]["max_tokens"] == 8000 and sent[1]["max_tokens"] == 24000
    assert len(sent[1]["messages"]) == 2  # no repair prompt: the model never got to answer


def test_busy_judge_flags_the_draft_instead_of_stopping():
    from conftest import brief, facts, good_draft

    from signal_agent.llm import ProviderBusy
    from signal_agent.models import EvidenceItem, JudgeScores
    from signal_agent.pipeline.verify import verify

    class BusyJudge(FakeLLM):
        def structured(self, *, schema, **kw):
            if schema is JudgeScores:
                raise ProviderBusy("judge busy")
            return super().structured(schema=schema, **kw)

    ev = {"ex-agents": EvidenceItem(id="ex-agents", text="Built LLM agents in 12 production workflows",
                                    metrics=["12 production workflows"]),
          "ex-eval": EvidenceItem(id="ex-eval", text="50-question eval harness, 46 of 50 correct",
                                  metrics=["50-question", "46 of 50"])}
    ver, _ = verify(BusyJudge(), good_draft(), facts=facts(), brief=brief(), evidence=ev, post_text=POST,
                    post_url=POST_URL, candidate_facts={}, denylist=[])
    assert not ver.passed and ver.judge is None
    assert any("judge unavailable" in p for p in ver.problems)
