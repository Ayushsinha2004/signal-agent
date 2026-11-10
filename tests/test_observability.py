"""Langfuse export: off without keys, one observation per stage on the lead's trace, and never breaks a step."""
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import POST, POST_URL, FakeLLM

from signal_agent import observability, tracing
from signal_agent.collectors import save_posts
from signal_agent.models import RawPost, State
from signal_agent.observability import LangfuseExporter
from signal_agent.pipeline.runner import create_leads, process, sync_profile


class FakeObs:
    def __init__(self, log: list, name: str) -> None:
        self.log, self.name = log, name

    def update(self, **kw: Any) -> None:
        self.log.append(("update", self.name, kw))

    def end(self) -> None:
        self.log.append(("end", self.name))


class FakeClient:
    def __init__(self, fail: bool = False) -> None:
        self.log: list = []
        self.fail = fail

    def start_observation(self, **kw: Any) -> FakeObs:
        if self.fail:
            raise ConnectionError("langfuse down")
        self.log.append(("start", kw["name"], kw["trace_context"]["trace_id"]))
        return FakeObs(self.log, kw["name"])

    def flush(self) -> None:
        if self.fail:
            raise ConnectionError("langfuse down")


def test_off_without_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    observability.get_exporter.cache_clear()
    try:
        assert observability.get_exporter() is None
        observability.flush()  # no-op, no error
    finally:
        observability.get_exporter.cache_clear()


@pytest.mark.db
@pytest.mark.parametrize("fail", [False, True])
def test_pipeline_exports_one_observation_per_stage(db, example_profile, monkeypatch, fail):
    client = FakeClient(fail=fail)
    monkeypatch.setattr(tracing, "get_exporter", lambda: LangfuseExporter(client))
    sync_profile(db, example_profile)
    save_posts(db, [RawPost(source="hn", external_id="1", url=POST_URL, author="f", posted_at=datetime.now(UTC),
                            text=POST)])
    create_leads(db, example_profile.id)
    lead = db.execute("select id, trace_id from leads").fetchone()

    assert process(db, FakeLLM(), example_profile, lead["id"]) is State.REVIEW  # export failure changes nothing
    if fail:
        assert client.log == []
        return
    starts = [e for e in client.log if e[0] == "start"]
    assert [s[1] for s in starts] == ["pre_rules", "normalise", "post_rules", "classify", "research", "match",
                                      "draft_1", "verify_1"]
    assert {s[2] for s in starts} == {lead["trace_id"]}
    updates = {e[1]: e[2] for e in client.log if e[0] == "update"}
    assert "model" not in updates["pre_rules"]  # no model call: plain span data only
    assert updates["normalise"]["model"] == "claude-haiku-5-5"
    assert updates["normalise"]["usage_details"]["input"] == 1000
    assert updates["normalise"]["cost_details"]["total"] > 0
    assert len([e for e in client.log if e[0] == "end"]) == 8
