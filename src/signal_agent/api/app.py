"""Review UI: a small JSON API plus one static page (PLAN.md F9/F10, M5).

Every human action goes through the same state machine as the pipeline (`runner.move`), so the UI
cannot put a lead in a state the pipeline would call illegal. Each action is also written to
`review_events`, which keeps edits as diffs and measures review time per lead.
"""
from __future__ import annotations

import difflib
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import connect, jsonb
from ..models import TRANSITIONS, State
from ..pipeline.runner import IllegalTransition, move

STATIC = Path(__file__).parent / "static"
EDIT_TAGS = ("tone", "wrong_fact", "too_long", "generic", "other")

# action -> (states it is offered from, target state)
ACTIONS: dict[str, tuple[set[State], State]] = {
    "approve": ({State.REVIEW}, State.APPROVED),
    "reject": ({State.REVIEW}, State.DEAD),
    "redraft": ({State.REVIEW}, State.QUALIFIED),  # back into the pipeline on the next `signal run`
    "sent": ({State.APPROVED}, State.SENT),
    "replied": ({State.SENT}, State.REPLIED),
    "interview": ({State.REPLIED}, State.INTERVIEW),
    "dead": ({State.APPROVED, State.SENT, State.REPLIED}, State.DEAD),
    "rescue": ({State.REJECTED}, State.REVIEW),  # a rule or classifier was wrong; look at it by hand
    "retry": ({State.FAILED}, State.NEW),
}
for _name, (_froms, _to) in ACTIONS.items():  # the table above must agree with the state machine
    assert all(_to in TRANSITIONS[f] for f in _froms), _name

app = FastAPI(title="Signal review", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def basic_auth(request: Request, call_next: Any) -> Response:
    """Optional HTTP basic auth (user `signal`), on when SIGNAL_UI_PASSWORD is set. Needed once deployed."""
    password = get_settings().signal_ui_password
    if password and request.url.path != "/healthz":
        import base64

        ok = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, given = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, "signal") and secrets.compare_digest(given, password)
            except ValueError:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="signal"'})
    response: Response = await call_next(request)
    return response


def get_conn() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with connect() as conn:
        yield conn


Conn = psycopg.Connection[dict[str, Any]]


class DraftEdit(BaseModel):
    subject: str | None = None
    body: str
    connection_note: str | None = None


class ActionIn(BaseModel):
    action: Literal["approve", "reject", "redraft", "sent", "replied", "interview", "dead", "rescue", "retry"]
    note: str | None = Field(None, max_length=2000)
    tags: list[str] = Field(default_factory=list, description=f"edit tags on approve: {', '.join(EDIT_TAGS)}")
    reply_kind: str | None = Field(None, description="replied: positive | neutral | negative")


def _event(conn: Conn, lead_id: int, action: str, frm: str | None, to: str | None, detail: dict[str, Any]) -> None:
    conn.execute("insert into review_events (lead_id, action, from_state, to_state, detail) values (%s,%s,%s,%s,%s)",
                 (lead_id, action, frm, to, jsonb(detail)))


def _lead_or_404(conn: Conn, lead_id: int) -> dict[str, Any]:
    lead = conn.execute("""select l.*, r.source, r.url, r.author, r.posted_at, r.text
                           from leads l join raw_posts r on r.id = l.raw_post_id where l.id = %s""",
                        (lead_id,)).fetchone()
    if lead is None:
        raise HTTPException(404, "no such lead")
    return lead


def _latest_draft(conn: Conn, lead_id: int) -> dict[str, Any] | None:
    return conn.execute("select * from drafts where lead_id = %s order by version desc limit 1",
                        (lead_id,)).fetchone()


def _diff(before: str, after: str) -> str:
    return "\n".join(difflib.unified_diff(before.splitlines(), after.splitlines(), "draft", "edited", lineterm=""))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/summary")
def summary(conn: Conn = Depends(get_conn)) -> dict[str, Any]:
    states = {r["state"]: r["n"] for r in conn.execute("select state, sum(n)::int n from v_funnel group by 1")}
    cost = conn.execute("""select coalesce(sum(cost_usd), 0)::float total,
                                  count(*) filter (where status = 'error')::int errors from step_runs""").fetchone()
    per_lead = conn.execute("""select percentile_cont(0.5) within group (order by cost_usd)::float median
                               from v_cost_per_lead where state not in ('NEW', 'REJECTED', 'DUPLICATE')""").fetchone()
    review = conn.execute("select percentile_cont(0.5) within group (order by seconds)::float median, count(*)::int n "
                          "from v_review_seconds").fetchone()
    assert cost is not None and per_lead is not None and review is not None
    return {"states": states, "spend_usd": cost["total"], "step_errors": cost["errors"],
            "median_cost_per_lead_usd": per_lead["median"], "median_review_seconds": review["median"],
            "reviewed": review["n"]}


@app.get("/api/leads")
def list_leads(state: str = "REVIEW", conn: Conn = Depends(get_conn)) -> list[dict[str, Any]]:
    states = [s.strip().upper() for s in state.split(",") if s.strip()]
    if not all(s in State.__members__ for s in states):
        raise HTTPException(400, f"unknown state in {state!r}")
    rows = conn.execute("""
        select l.id, l.state, l.facts->>'company' company, l.facts->'roles' roles, l.facts->>'location_text' location,
               l.fit->>'confidence' confidence, l.reject_reason, l.cost_usd::float cost_usd, l.updated_at,
               r.source, r.url, r.posted_at,
               exists (select 1 from jsonb_array_elements_text(coalesce(l.rule_verdict->'flags', '[]'::jsonb)) f
                       where f like 'stretch%%') stretch,
               d.verification->>'passed' verified
        from leads l join raw_posts r on r.id = l.raw_post_id
        left join lateral (select verification from drafts where lead_id = l.id order by version desc limit 1) d
          on true
        where l.state = any(%s) order by l.updated_at desc limit 500""", (states,)).fetchall()
    return rows


@app.get("/api/leads/{lead_id}")
def lead_detail(lead_id: int, conn: Conn = Depends(get_conn)) -> dict[str, Any]:
    lead = _lead_or_404(conn, lead_id)
    drafts = conn.execute("select * from drafts where lead_id = %s order by version", (lead_id,)).fetchall()
    steps = conn.execute("""select stage, status, model, input_tokens, output_tokens, cost_usd::float cost_usd,
                                   latency_ms, error, detail, started_at
                            from step_runs where lead_id = %s order by id""", (lead_id,)).fetchall()
    events = conn.execute("select action, from_state, to_state, detail, created_at from review_events "
                          "where lead_id = %s order by id", (lead_id,)).fetchall()
    outcome = conn.execute("select * from outcomes where lead_id = %s", (lead_id,)).fetchone()
    cited = {eid for d in drafts for c in d["claims"] for eid in [c.get("evidence_id")] if eid}
    evidence = conn.execute("select id, text, metrics from evidence where profile_id = %s and id = any(%s)",
                            (lead["profile_id"], list(cited))).fetchall()
    state = State(lead["state"])
    lead["cost_usd"] = float(lead["cost_usd"])
    return {"lead": lead, "drafts": drafts, "steps": steps, "events": events, "outcome": outcome,
            "evidence": evidence, "actions": [a for a, (froms, _) in ACTIONS.items() if state in froms]}


@app.post("/api/leads/{lead_id}/view", status_code=204)
def mark_viewed(lead_id: int, conn: Conn = Depends(get_conn)) -> None:
    lead = _lead_or_404(conn, lead_id)
    _event(conn, lead_id, "view", lead["state"], None, {})
    conn.commit()


@app.put("/api/leads/{lead_id}/draft")
def edit_draft(lead_id: int, edit: DraftEdit, conn: Conn = Depends(get_conn)) -> dict[str, Any]:
    lead = _lead_or_404(conn, lead_id)
    if lead["state"] not in (State.REVIEW, State.APPROVED):
        raise HTTPException(409, f"drafts can only be edited in REVIEW or APPROVED, lead is {lead['state']}")
    d = _latest_draft(conn, lead_id)
    if d is None:
        raise HTTPException(409, "lead has no draft")
    before = d["edited_body"] or d["body"]
    changed = (before, d["subject"], d["connection_note"]) != (
        edit.body, edit.subject or d["subject"], edit.connection_note or d["connection_note"])
    if not changed:  # approve saves first; an untouched draft must not count as edited
        return {"draft_version": d["version"], "changed": False}
    conn.execute("update drafts set edited_body = %s, subject = coalesce(%s, subject), "
                 "connection_note = coalesce(%s, connection_note) where id = %s",
                 (None if edit.body == d["body"] else edit.body, edit.subject, edit.connection_note, d["id"]))
    _event(conn, lead_id, "edit", lead["state"], None,
           {"draft_version": d["version"], "diff": _diff(before, edit.body)})
    conn.commit()
    return {"draft_version": d["version"], "changed": True}


@app.post("/api/leads/{lead_id}/action")
def act(lead_id: int, body: ActionIn, conn: Conn = Depends(get_conn)) -> dict[str, Any]:
    lead = _lead_or_404(conn, lead_id)
    cur = State(lead["state"])
    froms, to = ACTIONS[body.action]
    if cur not in froms:
        raise HTTPException(409, f"cannot {body.action} a lead in {cur}")
    bad_tags = set(body.tags) - set(EDIT_TAGS)
    if bad_tags:
        raise HTTPException(400, f"unknown tags: {sorted(bad_tags)}")
    detail: dict[str, Any] = {"note": body.note} if body.note else {}
    now = datetime.now(UTC)
    d = _latest_draft(conn, lead_id)

    if body.action in ("approve", "reject") and d is not None:
        conn.execute("update drafts set status = %s where id = %s",
                     ("approved" if body.action == "approve" else "rejected", d["id"]))
        if body.action == "approve":
            original = d["body"]
            final = d["edited_body"] or original
            detail |= {"draft_version": d["version"], "tags": body.tags, "edited": final != original,
                       "edit_ratio": round(1 - difflib.SequenceMatcher(None, original, final).ratio(), 3)}
    if body.action == "sent":
        conn.execute("insert into outcomes (lead_id, sent_at, notes) values (%s,%s,%s) on conflict (lead_id) "
                     "do update set sent_at = excluded.sent_at", (lead_id, now, body.note))
    elif body.action == "replied":
        conn.execute("update outcomes set replied_at = %s, reply_kind = %s where lead_id = %s",
                     (now, body.reply_kind, lead_id))
    elif body.action == "interview":
        conn.execute("update outcomes set interview_at = %s where lead_id = %s", (now, lead_id))

    _event(conn, lead_id, body.action, cur.value, to.value, detail)
    extra: dict[str, Any] = {}
    if body.action in ("reject", "dead") and body.note:
        extra["reject_reason"] = body.note[:500]
    if body.action in ("rescue", "retry", "redraft"):
        extra["reject_reason"] = None
    try:
        move(conn, lead_id, cur, to, **extra)  # commits the action, the event and the outcome together
    except IllegalTransition as e:  # pragma: no cover - guarded by the ACTIONS assertion above
        raise HTTPException(409, str(e)) from e
    return {"id": lead_id, "state": to.value}


@app.get("/api/export.csv", include_in_schema=False)
def export_csv(conn: Conn = Depends(get_conn)) -> Response:
    """Approved leads with their final (edited if edited) message, ready to paste."""
    import csv
    import io

    rows = conn.execute("""
        select l.id, l.facts->>'company' company, r.url post_url, l.facts->'contact'->'emails' emails,
               d.channel, d.subject, coalesce(d.edited_body, d.body) body, d.connection_note
        from leads l join raw_posts r on r.id = l.raw_post_id
        join lateral (select * from drafts where lead_id = l.id order by version desc limit 1) d on true
        where l.state = 'APPROVED' order by l.id""").fetchall()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["id", "company", "post_url", "emails", "channel", "subject", "body",
                                        "connection_note"])
    w.writeheader()
    for r in rows:
        w.writerow({**r, "emails": " ".join(r["emails"] or [])})
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"content-disposition": "attachment; filename=signal-approved.csv"})
