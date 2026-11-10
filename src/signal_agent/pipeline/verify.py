"""VERIFY: deterministic checks first (PLAN.md §3.4), then an LLM judge that is not the drafter's model."""
from __future__ import annotations

import re

from ..llm import LLM, LLMResult, ProviderBusy, Tier, Usage
from ..models import CompanyBrief, DraftOut, EvidenceItem, JudgeScores, PostFacts, Verification
from ..prompts import prompt

NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?:\s?(?:k|m|%|s|x))?(?![\w])", re.I)
DASHES = re.compile(r"[–—]")
NOT_BUT = re.compile(r"\bnot (?:just |only |simply )?\w[^.,;]{0,40}\bbut\b", re.I)
BUZZ = ["passionate", "synergy", "leverage", "cutting-edge", "cutting edge", "rockstar", "ninja", "game-changer",
        "game changer", "thrilled", "delve", "testament", "seamless", "world-class"]
SMALL_NUMBERS = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "15", "30"}  # counts like "a 15-minute call"


def _numbers(text: str) -> set[str]:
    return {m.group(1).replace(",", "") for m in NUM.finditer(text)}


def check(d: DraftOut, *, facts: PostFacts, brief: CompanyBrief, evidence: dict[str, EvidenceItem],
          post_text: str, post_url: str | None, candidate_facts: dict[str, str], denylist: list[str]) -> tuple[
        float, list[str]]:
    problems: list[str] = []
    text_all = f"{d.subject}\n{d.body}\n{d.connection_note}"

    # 1. citations
    source_urls = {s.url for s in brief.sources} | {u for u in [post_url] if u} | set(facts.contact.apply_urls)
    ok = 0
    for c in d.claims:
        if c.evidence_id and c.evidence_id in evidence or c.source_url and c.source_url in source_urls:
            ok += 1
        else:
            problems.append(f"uncited or unknown citation: '{c.text[:60]}'")
    grounded = ok / len(d.claims) if d.claims else 0.0
    if not d.claims:
        problems.append("no claims listed")

    # 2. every number must exist in a source we gave the model
    allowed = _numbers(post_text) | _numbers(brief.model_dump_json()) | _numbers(" ".join(candidate_facts.values()))
    for e in evidence.values():
        allowed |= _numbers(e.text + " " + " ".join(e.metrics))
    for n in _numbers(text_all) - allowed - SMALL_NUMBERS:
        problems.append(f"number not found in any source: {n}")

    # 3. style and safety
    if DASHES.search(text_all):
        problems.append("contains an em/en dash")
    if NOT_BUT.search(d.body):
        problems.append("uses a 'not X but Y' construction")
    for w in BUZZ:
        if w in text_all.lower():
            problems.append(f"buzzword: {w}")
    for w in denylist:
        if w.lower() in text_all.lower():
            problems.append("mentions a private (denylisted) name")
    words = len(d.body.split())
    if not 50 <= words <= 120:
        problems.append(f"body is {words} words (want 50-110)")
    if len(d.connection_note) > 300:
        problems.append(f"connection note is {len(d.connection_note)} chars (max 300)")
    if len(d.subject) > 70:
        problems.append(f"subject is {len(d.subject)} chars")
    if facts.company.lower() != "unknown" and facts.company.split()[0].lower() not in d.body.lower():
        problems.append("body does not name the company")
    return grounded, problems


def judge(llm: LLM, d: DraftOut, post_text: str) -> LLMResult[JudgeScores]:
    user = f"Post:\n{post_text[:5000]}\n\nSubject: {d.subject}\n\nMessage:\n{d.body}"
    return llm.structured(tier=Tier.JUDGE, system=prompt("judge"), user=user, schema=JudgeScores, effort="low")


def verify(llm: LLM | None, d: DraftOut, **kw: object) -> tuple[Verification, Usage]:
    grounded, problems = check(d, **kw)  # type: ignore[arg-type]
    usage = Usage()
    scores = None
    if llm is not None and not problems:
        try:
            res = judge(llm, d, str(kw["post_text"]))
        except ProviderBusy:  # no score is not a pass: flag it for review and for `signal reverify`
            problems.append("judge unavailable (provider busy): run `signal reverify` later")
        else:
            scores, usage = res.value, res.usage
            mean = (scores.relevance + scores.specificity + scores.tone + scores.honesty) / 4
            if mean < 3.5 or scores.honesty < 3:
                problems.append(f"judge: {scores.feedback}")
    passed = not problems and grounded == 1.0
    return Verification(grounded_pct=grounded, problems=problems, judge=scores, passed=passed), usage
