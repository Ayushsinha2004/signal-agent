"""Review API over real Postgres: the UI walks a lead REVIEW -> APPROVED -> SENT -> REPLIED, and illegal moves 409."""
from datetime import UTC, datetime

import pytest
from conftest import POST, POST_URL, FakeLLM
from fastapi.testclient import TestClient

from signal_agent.api.app import app, get_conn
from signal_agent.collectors import save_posts
from signal_agent.models import RawPost, State
from signal_agent.pipeline.runner import create_leads, process, sync_profile

pytestmark = pytest.mark.db


@pytest.fixture
def client(db, example_profile):
    sync_profile(db, example_profile)
    save_posts(db, [RawPost(source="hn", external_id="1", url=POST_URL, author="f", posted_at=datetime.now(UTC),
                            text=POST)])
    create_leads(db, example_profile.id)
    lead_id = db.execute("select id from leads").fetchone()["id"]
    assert process(db, FakeLLM(), example_profile, lead_id) is State.REVIEW
    app.dependency_overrides[get_conn] = lambda: db
    yield TestClient(app), lead_id
    app.dependency_overrides.clear()


def test_page_and_health(client):
    c, _ = client
    assert c.get("/healthz").json() == {"status": "ok"}
    page = c.get("/")
    assert page.status_code == 200 and "Signal review" in page.text


def test_list_and_detail(client):
    c, lead_id = client
    rows = c.get("/api/leads?state=REVIEW").json()
    assert [r["id"] for r in rows] == [lead_id]
    assert rows[0]["company"] == "Acme AI" and rows[0]["verified"] == "true"
    assert rows[0]["stretch"] is False
    d = c.get(f"/api/leads/{lead_id}").json()
    assert d["lead"]["state"] == "REVIEW"
    assert set(d["actions"]) == {"approve", "reject", "redraft"}
    assert len(d["drafts"]) == 1 and len(d["steps"]) == 8
    assert {e["id"] for e in d["evidence"]} == {"ex-agents", "ex-eval"}
    assert c.get("/api/leads?state=NOPE").status_code == 400
    assert c.get("/api/leads/999999").status_code == 404


def test_edit_approve_send_reply(client, db):
    c, lead_id = client
    assert c.post(f"/api/leads/{lead_id}/view").status_code == 204
    original = c.get(f"/api/leads/{lead_id}").json()["drafts"][0]
    same = {"subject": original["subject"], "body": original["body"], "connection_note": original["connection_note"]}
    assert c.put(f"/api/leads/{lead_id}/draft", json=same).json()["changed"] is False  # no event, not "edited"
    r = c.put(f"/api/leads/{lead_id}/draft", json={"body": "Hi, shorter message.", "connection_note": "Hi there"})
    assert r.json() == {"draft_version": 1, "changed": True}

    r = c.post(f"/api/leads/{lead_id}/action", json={"action": "approve", "tags": ["too_long"]})
    assert r.json() == {"id": lead_id, "state": "APPROVED"}
    draft = db.execute("select status, edited_body, body from drafts where lead_id = %s", (lead_id,)).fetchone()
    assert draft["status"] == "approved" and draft["edited_body"] == "Hi, shorter message."
    assert draft["body"] != draft["edited_body"]  # the model's original is kept for the edit-distance metric

    csv_text = c.get("/api/export.csv").text
    assert "Hi, shorter message." in csv_text and "jobs@acme.ai" in csv_text

    assert c.post(f"/api/leads/{lead_id}/action", json={"action": "sent"}).json()["state"] == "SENT"
    assert c.post(f"/api/leads/{lead_id}/action",
                  json={"action": "replied", "reply_kind": "positive"}).json()["state"] == "REPLIED"
    out = db.execute("select sent_at, replied_at, reply_kind from outcomes where lead_id = %s", (lead_id,)).fetchone()
    assert out["sent_at"] and out["replied_at"] and out["reply_kind"] == "positive"

    events = [e["action"] for e in c.get(f"/api/leads/{lead_id}").json()["events"]]
    assert events == ["view", "edit", "approve", "sent", "replied"]
    approve = db.execute("select detail from review_events where action = 'approve'").fetchone()["detail"]
    assert approve["edited"] is True and approve["tags"] == ["too_long"] and approve["edit_ratio"] > 0
    edit = db.execute("select detail from review_events where action = 'edit'").fetchone()["detail"]
    assert "+Hi, shorter message." in edit["diff"]

    s = c.get("/api/summary").json()
    assert s["states"]["REPLIED"] == 1 and s["reviewed"] == 1 and s["spend_usd"] > 0


def test_illegal_actions_are_refused(client):
    c, lead_id = client
    assert c.post(f"/api/leads/{lead_id}/action", json={"action": "sent"}).status_code == 409
    assert c.post(f"/api/leads/{lead_id}/action", json={"action": "approve", "tags": ["nope"]}).status_code == 400
    assert c.post(f"/api/leads/{lead_id}/action", json={"action": "teleport"}).status_code == 422
    assert c.post(f"/api/leads/{lead_id}/action", json={"action": "reject", "note": "agency"}).json()["state"] == "DEAD"
    assert c.put(f"/api/leads/{lead_id}/draft", json={"body": "x"}).status_code == 409
    assert c.get(f"/api/leads/{lead_id}").json()["lead"]["reject_reason"] == "agency"


def test_redraft_goes_back_through_the_pipeline(client, db, example_profile):
    c, lead_id = client
    assert c.post(f"/api/leads/{lead_id}/action", json={"action": "redraft"}).json()["state"] == "QUALIFIED"
    assert process(db, FakeLLM(), example_profile, lead_id) is State.REVIEW
    versions = [r["version"] for r in db.execute("select version from drafts where lead_id = %s order by version",
                                                    (lead_id,)).fetchall()]
    assert versions == [1, 2]  # the first draft is kept, the redraft is a new version


def test_basic_auth_when_password_set(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("SIGNAL_UI_PASSWORD", "pw-for-test")
    assert c.get("/api/summary").status_code == 401
    assert c.get("/healthz").status_code == 200
    assert c.get("/api/summary", auth=("signal", "pw-for-test")).status_code == 200
    assert c.get("/api/summary", auth=("signal", "wrong")).status_code == 401
