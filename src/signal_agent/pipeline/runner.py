"""The state machine (ADR-001). One lead moves through stages; each stage is persisted and traced."""
from __future__ import annotations

import re
from typing import Any

import psycopg
import structlog

from ..db import jsonb
from ..llm import LLM, QuotaExhausted
from ..models import TRANSITIONS, CompanyBrief, DraftOut, EvidenceItem, Fit, Match, PostFacts, State, Verification
from ..profile import Profile
from ..tracing import step
from . import stages
from .rules import post_rules, pre_rules
from .verify import verify

log = structlog.get_logger()
# states `signal run` advances, furthest along first
ACTIVE_ORDER = [State.MATCHED.value, State.RESEARCHED.value, State.QUALIFIED.value, State.NORMALISED.value,
                State.NEW.value]
TERMINAL = {State.REJECTED, State.DUPLICATE, State.REVIEW, State.FAILED, State.DEAD}


class IllegalTransition(Exception):
    pass


def slug(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"^https?://(www\.)?", "", s.lower()).split("/")[0]
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or None


def move(conn: psycopg.Connection[Any], lead_id: int, cur: State, new: State, **cols: Any) -> None:
    if new not in TRANSITIONS[cur]:
        raise IllegalTransition(f"{cur} -> {new}")
    sets = ", ".join(f"{k} = %s" for k in cols)
    vals = [jsonb(v) if hasattr(v, "model_dump") or isinstance(v, (dict, list)) else v for v in cols.values()]
    conn.execute(f"update leads set state = %s, updated_at = now(){', ' + sets if sets else ''} where id = %s",
                 [new.value, *vals, lead_id])
    conn.commit()


def sync_profile(conn: psycopg.Connection[Any], p: Profile) -> None:
    conn.execute("insert into profiles (id, name, criteria) values (%s,%s,%s) on conflict (id) do update "
                 "set name = excluded.name, criteria = excluded.criteria", (p.id, p.name, jsonb(p.criteria)))
    for e in p.evidence:
        conn.execute("""insert into evidence (id, profile_id, text, metrics, skills, tsv)
                        values (%s,%s,%s,%s,%s, to_tsvector('english', %s))
                        on conflict (profile_id, id) do update set text = excluded.text, metrics = excluded.metrics,
                        skills = excluded.skills, tsv = excluded.tsv, updated_at = now()""",
                     (e.id, p.id, e.text, e.metrics, e.skills, e.text + " " + " ".join(e.skills)))
    conn.commit()


def create_leads(conn: psycopg.Connection[Any], profile_id: str) -> int:
    rows = conn.execute("""insert into leads (profile_id, raw_post_id)
                           select %s, r.id from raw_posts r
                           where not exists (select 1 from leads l where l.raw_post_id = r.id and l.profile_id = %s)
                           returning id""", (profile_id, profile_id)).fetchall()
    conn.commit()
    return len(rows)


def retrieve_evidence(conn: psycopg.Connection[Any], p: Profile, facts: PostFacts, k: int = 14) -> list[EvidenceItem]:
    terms = [t for t in facts.stack + facts.roles if t] or ["engineer"]
    q = " OR ".join(re.sub(r"[^\w\s.+#-]", " ", t) for t in terms[:20])
    rows = conn.execute("""select id from evidence where profile_id = %s
                           order by ts_rank(tsv, websearch_to_tsquery('english', %s)) desc limit %s""",
                        (p.id, q, k)).fetchall()
    by_id = p.evidence_by_id()
    return [by_id[r["id"]] for r in rows if r["id"] in by_id]


def process(conn: psycopg.Connection[Any], llm: LLM, p: Profile, lead_id: int, channel: str = "inmail") -> State:
    """Advance one lead until it reaches a terminal or review state. Safe to call again after a failure."""
    while True:
        lead = conn.execute("select l.*, r.text, r.url, r.posted_at from leads l join raw_posts r "
                            "on r.id = l.raw_post_id where l.id = %s", (lead_id,)).fetchone()
        assert lead is not None
        state = State(lead["state"])
        if state in TERMINAL:
            return state
        try:
            _advance(conn, llm, p, lead, state, channel)
        except QuotaExhausted:  # not the lead's fault: leave it where it is and stop the run
            conn.rollback()
            raise
        except Exception as e:  # recorded in step_runs by `step`; the lead lands in FAILED with the reason
            log.warning("stage_failed", lead=lead_id, state=state.value, error=str(e)[:200])
            conn.rollback()
            if State.FAILED in TRANSITIONS[state]:
                move(conn, lead_id, state, State.FAILED, reject_reason=f"{state.value}: {e}"[:500])
            return State.FAILED


def _advance(conn: psycopg.Connection[Any], llm: LLM, p: Profile, lead: dict[str, Any], state: State,
             channel: str) -> None:
    lid, text = lead["id"], lead["text"]
    if state is State.NEW:
        with step(conn, lid, "pre_rules") as rec:
            v = pre_rules(text, lead["posted_at"], p.criteria)
            rec.detail = v.model_dump()
        if not v.passed:
            move(conn, lid, state, State.REJECTED, rule_verdict=v, reject_reason="; ".join(v.reasons))
            return
        with step(conn, lid, "normalise") as rec:
            res = stages.normalise(llm, text)
            rec.usage = res.usage
        move(conn, lid, state, State.NORMALISED, facts=res.value)

    elif state is State.NORMALISED:
        facts = PostFacts(**lead["facts"])
        with step(conn, lid, "post_rules") as rec:
            v = post_rules(facts, p.criteria)
            rec.detail = v.model_dump()
        if not v.passed:
            move(conn, lid, state, State.REJECTED, rule_verdict=v, reject_reason="; ".join(v.reasons))
            return
        with step(conn, lid, "classify") as rec:
            cres = stages.classify(llm, text, facts)
            rec.usage, fit = cres.usage, cres.value
        reasons = []
        if fit.is_recruiter_or_agency_repost:
            reasons.append("recruiter or agency repost")
        if not fit.is_direct_hire:
            reasons.append("not posted by the hiring company")
        if fit.india_eligible == "no":
            reasons.append("not open to candidates in India")
        if reasons:
            move(conn, lid, state, State.REJECTED, rule_verdict=v, fit=fit, reject_reason="; ".join(reasons))
            return
        ckey, rkey = slug(facts.company_domain) or slug(facts.company), slug(facts.roles[0] if facts.roles else None)
        dup = conn.execute("""select id from leads where profile_id = %s and company_key = %s and role_key = %s
                              and id <> %s and state <> 'DUPLICATE'""", (p.id, ckey, rkey, lid)).fetchone()
        if dup:
            move(conn, lid, state, State.DUPLICATE, company_key=ckey, role_key=None,
                 reject_reason=f"duplicate of lead {dup['id']}")
            return
        if fit.confidence < 0.6:
            move(conn, lid, state, State.REVIEW, rule_verdict=v, fit=fit, company_key=ckey, role_key=rkey,
                 reject_reason="low classifier confidence: check by hand")
            return
        move(conn, lid, state, State.QUALIFIED, rule_verdict=v, fit=fit, company_key=ckey, role_key=rkey)

    elif state is State.QUALIFIED:
        facts = PostFacts(**lead["facts"])
        with step(conn, lid, "research") as rec:
            bres = stages.research(llm, text, facts, lead["url"])
            rec.usage, rec.detail = bres.usage, {"tool_calls": len(bres.transcript)}
        move(conn, lid, state, State.RESEARCHED, brief=bres.value)

    elif state is State.RESEARCHED:
        facts = PostFacts(**lead["facts"])
        cands = retrieve_evidence(conn, p, facts)
        with step(conn, lid, "match") as rec:
            mres = stages.match(llm, text, cands)
            rec.usage, rec.detail = mres.usage, {"candidates": [c.id for c in cands]}
        move(conn, lid, state, State.MATCHED, match=mres.value)

    elif state is State.MATCHED:
        facts, brief, m = PostFacts(**lead["facts"]), CompanyBrief(**lead["brief"]), Match(**lead["match"])
        candidate = {"first_name": p.name.split()[0], "facts": p.facts}
        feedback: str | None = None
        ver: Verification | None = None
        d: DraftOut | None = None
        prior = conn.execute("select coalesce(max(version), 0) v from drafts where lead_id = %s", (lid,)).fetchone()
        base = prior["v"] if prior else 0  # a redraft from review keeps the earlier versions
        for attempt in (1, 2):
            with step(conn, lid, f"draft_{attempt}") as rec:
                dres = stages.draft(llm, text=text, facts=facts, brief=brief, m=m, evidence=p.evidence,
                                    candidate=candidate, channel=channel, feedback=feedback)
                rec.usage, d = dres.usage, dres.value
            with step(conn, lid, f"verify_{attempt}") as rec:
                ver, rec.usage = verify(llm, d, facts=facts, brief=brief, evidence=p.evidence_by_id(),
                                        post_text=text, post_url=lead["url"],
                                        candidate_facts={k: str(v) for k, v in p.facts.items()},
                                        denylist=p.denylist)
                rec.detail = ver.model_dump()
            save_draft(conn, lid, base + attempt, d, ver)
            if ver.passed:
                break
            feedback = "; ".join(ver.problems)
        move(conn, lid, state, State.DRAFTED)
        move(conn, lid, State.DRAFTED, State.REVIEW,
             reject_reason=None if ver and ver.passed else "draft failed verification twice: edit by hand")


def save_draft(conn: psycopg.Connection[Any], lead_id: int, version: int, d: DraftOut, ver: Any) -> None:
    conn.execute("""insert into drafts (lead_id, version, channel, subject, body, connection_note, claims, verification)
                    values (%s,%s,%s,%s,%s,%s,%s,%s) on conflict (lead_id, version) do update set
                    subject = excluded.subject, body = excluded.body, connection_note = excluded.connection_note,
                    claims = excluded.claims, verification = excluded.verification""",
                 (lead_id, version, d.channel, d.subject, d.body, d.connection_note,
                  jsonb([c.model_dump() for c in d.claims]), jsonb(ver)))
    conn.commit()


_ = (Fit,)  # re-exported for type checkers
