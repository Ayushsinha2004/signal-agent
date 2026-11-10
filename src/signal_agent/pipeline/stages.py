"""LLM-backed stages. Each is a pure function of its inputs plus an LLM; persistence lives in runner.py."""
from __future__ import annotations

import json
import re
from typing import Any

from ..llm import LLM, LLMResult, Tier, ToolSpec
from ..models import CompanyBrief, DraftOut, EvidenceItem, Fit, Match, PostFacts
from ..prompts import prompt
from ..tools.web import FetchError, fetch_url


def normalise(llm: LLM, text: str) -> LLMResult[PostFacts]:
    return llm.structured(tier=Tier.SMALL, system=prompt("normalise"), user=f"Post:\n{text[:12000]}",
                          schema=PostFacts, effort="low")


def classify(llm: LLM, text: str, facts: PostFacts) -> LLMResult[Fit]:
    user = f"Post:\n{text[:8000]}\n\nExtracted facts:\n{facts.model_dump_json()}"
    return llm.structured(tier=Tier.SMALL, system=prompt("classify"), user=user, schema=Fit, effort="low")


def research(llm: LLM, text: str, facts: PostFacts, post_url: str | None, max_pages: int = 4) -> LLMResult[CompanyBrief]:
    pages = {"n": 0}

    def _fetch(args: dict[str, Any]) -> str:
        if pages["n"] >= max_pages:
            return "page budget used up; write the brief now"
        pages["n"] += 1
        try:
            return fetch_url(str(args["url"]))
        except FetchError as e:
            return f"could not fetch: {e}"

    tools = [ToolSpec(
        name="fetch_url",
        description="Fetch a public web page and return its readable text (max 8000 chars). Use for the company "
                    "site, its about/careers pages, or links in the post.",
        input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"],
                      "additionalProperties": False},
        fn=_fetch,
    )]
    hints = [u for u in [facts.company_domain, post_url, *facts.contact.apply_urls] if u]
    user = (f"Company: {facts.company}\nRoles: {', '.join(facts.roles)}\nKnown URLs: {hints}\n\n"
            f"Post:\n{text[:6000]}")
    return llm.agent(tier=Tier.SMALL, system=prompt("research"), user=user, tools=tools, schema=CompanyBrief,
                     max_turns=max_pages + 1, effort="low")


def match(llm: LLM, text: str, candidates: list[EvidenceItem]) -> LLMResult[Match]:
    ev = "\n".join(f"- {e.id}: {e.text}" for e in candidates)
    user = f"Post:\n{text[:6000]}\n\nEvidence items:\n{ev}"
    res = llm.structured(tier=Tier.SMALL, system=prompt("match"), user=user, schema=Match, effort="low")
    valid = {e.id for e in candidates}
    for m in res.value.matches:  # never trust ids the model returns
        m.evidence_ids = [i for i in m.evidence_ids if i in valid]
    return res


NOTE_MAX = 300


def tidy(d: DraftOut) -> DraftOut:
    """Mechanical fixes that need no model call and change no facts: dashes become punctuation, and a connection
    note over the limit is cut back to its last complete sentence. Claims are left as they are."""
    def undash(s: str) -> str:
        s = re.sub(r"\s*[\u2013\u2014]\s*(?=\S)", ", ", s)  # "X – Y" / "X—Y" -> "X, Y"
        return s.replace("\u2013", "-").replace("\u2014", "-")
    d = d.model_copy(update={"subject": re.sub(r",\s", ": ", undash(d.subject), count=1)
                             if re.search(r"[\u2013\u2014]", d.subject) else d.subject,
                             "body": undash(d.body), "connection_note": undash(d.connection_note)})
    note = d.connection_note
    if len(note) > NOTE_MAX:
        cut = note[:NOTE_MAX]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        note = cut[:end + 1] if end > 80 else cut[:cut.rfind(" ")].rstrip(",;:") + "."
        d = d.model_copy(update={"connection_note": note})
    return d


def draft(llm: LLM, *, text: str, facts: PostFacts, brief: CompanyBrief, m: Match, evidence: list[EvidenceItem],
          candidate: dict[str, Any], channel: str, feedback: str | None = None) -> LLMResult[DraftOut]:
    used = {i for x in m.matches for i in x.evidence_ids}
    ev = [e for e in evidence if e.id in used] or evidence[:6]
    payload = {
        "candidate": candidate,
        "channel": channel,
        "post_facts": facts.model_dump(),
        "company_brief": brief.model_dump(),
        "requirement_matches": m.model_dump(),
        "evidence_items": [e.model_dump() for e in ev],
    }
    user = f"Write the message.\n\n{json.dumps(payload, ensure_ascii=False)}\n\nPost:\n{text[:5000]}"
    if feedback:
        user += f"\n\nA reviewer rejected the previous draft. Fix this: {feedback}"
    res = llm.structured(tier=Tier.LARGE, system=prompt("draft"), user=user, schema=DraftOut, effort="medium",
                         max_tokens=6000)
    res.value = tidy(res.value)
    return res
