"""End-to-end over real Postgres with a fake model: NEW -> ... -> REVIEW, plus idempotency and dedupe."""
from datetime import UTC, datetime

import pytest
from conftest import POST, POST_URL, FakeLLM, good_draft

from signal_agent.collectors import save_posts
from signal_agent.models import DraftOut, Fit, RawPost, State
from signal_agent.pipeline.runner import create_leads, process, sync_profile

pytestmark = pytest.mark.db


def _post(ext="1", text=POST):
    return RawPost(source="hn", external_id=ext, url=POST_URL, author="founder",
                   posted_at=datetime.now(UTC), text=text)


def test_full_pipeline_reaches_review(db, example_profile):
    sync_profile(db, example_profile)
    assert save_posts(db, [_post()]) == 1
    assert save_posts(db, [_post()]) == 0  # idempotent collect
    assert create_leads(db, example_profile.id) == 1
    assert create_leads(db, example_profile.id) == 0
    lead_id = db.execute("select id from leads").fetchone()["id"]
    llm = FakeLLM()
    assert process(db, llm, example_profile, lead_id) is State.REVIEW
    lead = db.execute("select * from leads where id = %s", (lead_id,)).fetchone()
    assert lead["reject_reason"] is None
    assert lead["match"]["matches"][0]["evidence_ids"] == ["ex-agents"]  # bogus id dropped
    d = db.execute("select * from drafts where lead_id = %s", (lead_id,)).fetchone()
    assert d["verification"]["passed"] is True
    stages = [r["stage"] for r in db.execute("select stage from step_runs order by id").fetchall()]
    assert stages == ["pre_rules", "normalise", "post_rules", "classify", "research", "match", "draft_1", "verify_1"]
    assert float(lead["cost_usd"]) > 0


def test_recruiter_is_rejected_by_classifier(db, example_profile):
    sync_profile(db, example_profile)
    save_posts(db, [_post()])
    create_leads(db, example_profile.id)
    lead_id = db.execute("select id from leads").fetchone()["id"]
    fit = Fit(role_family="ai_engineer", is_recruiter_or_agency_repost=True, is_direct_hire=False,
              india_eligible="yes", confidence=0.9, rationale="agency")
    assert process(db, FakeLLM({Fit: fit}), example_profile, lead_id) is State.REJECTED


def test_bad_draft_retries_then_lands_in_review_flagged(db, example_profile):
    sync_profile(db, example_profile)
    save_posts(db, [_post()])
    create_leads(db, example_profile.id)
    lead_id = db.execute("select id from leads").fetchone()["id"]
    bad: DraftOut = good_draft()
    bad.body = bad.body.replace("12 production", "99 production")
    llm = FakeLLM({DraftOut: bad})
    assert process(db, llm, example_profile, lead_id) is State.REVIEW
    assert llm.calls.count("DraftOut") == 2
    lead = db.execute("select reject_reason from leads where id = %s", (lead_id,)).fetchone()
    assert "failed verification" in lead["reject_reason"]


def test_duplicate_company_role(db, example_profile):
    sync_profile(db, example_profile)
    save_posts(db, [_post("1"), _post("2", POST + " (reposted)")])
    create_leads(db, example_profile.id)
    ids = [r["id"] for r in db.execute("select id from leads order by id").fetchall()]
    llm = FakeLLM()
    assert process(db, llm, example_profile, ids[0]) is State.REVIEW
    assert process(db, llm, example_profile, ids[1]) is State.DUPLICATE


def test_stage_error_lands_in_failed(db, example_profile):
    class Boom(FakeLLM):
        def agent(self, **kw):
            raise TimeoutError("site down")
    sync_profile(db, example_profile)
    save_posts(db, [_post()])
    create_leads(db, example_profile.id)
    lead_id = db.execute("select id from leads").fetchone()["id"]
    assert process(db, Boom(), example_profile, lead_id) is State.FAILED
    err = db.execute("select error from step_runs where stage = 'research'").fetchone()["error"]
    assert "site down" in err
