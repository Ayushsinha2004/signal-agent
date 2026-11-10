"""Qualification eval on the golden set (PLAN.md §5).

  uv run python evals/run_evals.py --stage rules     # free: deterministic pre-rules only
  uv run python evals/run_evals.py --stage qualify   # live model: pre-rules + normalise + post-rules + classify

Exits non-zero when a gate fails, so CI can block a prompt or rules change that makes things worse.
Ambiguous labels are reported separately and excluded from the gated numbers.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signal_agent.models import Criteria  # noqa: E402
from signal_agent.pipeline.rules import post_rules, pre_rules  # noqa: E402
from signal_agent.profile import load_criteria  # noqa: E402

GATES = {"rules": {"recall": 0.85}, "qualify": {"precision": 0.85, "recall": 0.80}}
STRETCH: list[str] = []  # posts the pipeline would draft as stretch leads (reported, not gated)
EVAL_NOW = datetime(2026, 11, 10, tzinfo=UTC)  # frozen so post age doesn't drift as the calendar moves


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def predict_rules(item: dict, criteria: Criteria) -> tuple[bool, str]:
    posted = datetime.fromisoformat(item["posted_at"]) if item.get("posted_at") else None
    v = pre_rules(item["text"], posted, criteria, now=EVAL_NOW)
    return v.passed, "; ".join(v.reasons)


def predict_qualify(item: dict, criteria: Criteria, llm) -> tuple[bool, str, float]:
    from signal_agent.pipeline import stages

    ok, why = predict_rules(item, criteria)
    if not ok:
        return False, why, 0.0
    n = stages.normalise(llm, item["text"])
    v = post_rules(n.value, criteria)
    cost = n.usage
    if not v.passed:
        return False, "; ".join(v.reasons), _cost(cost)
    stretch = v.stretch
    c = stages.classify(llm, item["text"], n.value)
    cost.add(c.usage)
    fit = c.value
    q = fit.is_direct_hire and not fit.is_recruiter_or_agency_repost and fit.india_eligible != "no"
    if q and stretch:  # drafted as a stretch lead, but not a core fit, which is what the labels measure
        STRETCH.append(item["external_id"])
        return False, "stretch: " + "; ".join(f for f in v.flags if f.startswith("stretch")), _cost(cost)
    return q, fit.rationale, _cost(cost)


def _cost(u) -> float:
    from signal_agent.llm import cost_usd
    return cost_usd(u.model, u.input_tokens, u.output_tokens, u.cache_read_tokens)


def score(rows: list[tuple[bool, bool]]) -> dict[str, float]:
    tp = sum(p and g for p, g in rows)
    fp = sum(p and not g for p, g in rows)
    fn = sum(g and not p for p, g in rows)
    tn = sum(not p and not g for p, g in rows)
    return {"n": len(rows), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else 1.0, "recall": tp / (tp + fn) if tp + fn else 1.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["rules", "qualify"], default="rules")
    ap.add_argument("--data", default=str(ROOT / "evals/data/posts_golden.jsonl"))
    ap.add_argument("--profile", default="ayush")
    args = ap.parse_args()

    criteria = load_criteria(args.profile, root=ROOT / "profiles")
    items = load(Path(args.data))
    llm = None
    if args.stage == "qualify":
        from signal_agent.llm.factory import make_llm
        llm = make_llm()

    from signal_agent.llm import QuotaExhausted

    gated, ambiguous, misses, spend, paused = [], [], [], 0.0, None
    for it in items:
        if args.stage == "rules":
            pred, why = predict_rules(it, criteria)
        else:
            try:
                pred, why, c = predict_qualify(it, criteria, llm)
            except QuotaExhausted as e:  # free-tier daily cap: report what we have, rerun later (cached)
                paused = str(e)
                break
            spend += c
        (ambiguous if it["ambiguous"] else gated).append((pred, it["qualify"]))
        if pred != it["qualify"]:
            misses.append({"id": it["external_id"], "gold": it["qualify"], "pred": pred,
                           "ambiguous": it["ambiguous"], "why": why[:160], "label": it["reason"]})

    m = score(gated)
    gate = GATES[args.stage]
    # for the rules stage only recall is gated: rules must not drop good posts; the LLM does the rest
    passed = all(m[k] >= v for k, v in gate.items()) and paused is None
    report = {"stage": args.stage, "gated": m, "ambiguous": score(ambiguous), "gates": gate, "passed": passed,
              "spend_usd": round(spend, 4), "misses": misses}
    if args.stage == "qualify":
        report["stretch_leads"] = STRETCH
    if llm is not None:
        report["models"] = {t: llm.model_for(t) for t in ("small", "large", "judge")}
        report["cache"] = {"hits": getattr(llm, "hits", None), "misses": getattr(llm, "misses", None)}
    if paused:
        report |= {"incomplete": True, "scored": len(gated) + len(ambiguous), "of": len(items), "paused": paused}
    print(json.dumps(report, indent=2))
    if not paused:
        _record(args.stage, report)
    return 2 if paused else 0 if passed else 1


def _record(stage: str, report: dict) -> None:
    try:
        from signal_agent.db import connect, jsonb
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             cwd=ROOT).stdout.strip() or None
        with connect() as conn:
            conn.execute("insert into eval_runs (git_sha, dataset, metrics, passed) values (%s,%s,%s,%s)",
                         (sha, f"posts_golden:{stage}", jsonb(report), report["passed"]))
    except Exception as e:  # CI without a database still gets the exit code
        print(f"(eval run not recorded: {type(e).__name__})", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
