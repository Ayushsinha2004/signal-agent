"""Judge calibration (PLAN.md §5.3): the judge must separate my real messages from damaged ones before I trust it.

    uv run python scripts/build_drafts_golden.py      # once: builds evals/data/private/drafts_golden.jsonl
    uv run python evals/judge_calibration.py --good 15 --bad 15

Score = the mean of the judge's four 1-5 scores. Gate: ROC AUC >= 0.9 (good above bad). Replies are cached,
so a run that hits the free-tier daily cap continues where it stopped (exit code 2 until complete).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signal_agent.llm import QuotaExhausted  # noqa: E402
from signal_agent.llm.factory import make_llm  # noqa: E402
from signal_agent.models import DraftOut  # noqa: E402
from signal_agent.pipeline.verify import judge  # noqa: E402

DATA = ROOT / "evals" / "data" / "private" / "drafts_golden.jsonl"
GATE = 0.9


def auc(good: list[float], bad: list[float]) -> float:
    """Probability a random good message outscores a random bad one (ties count half)."""
    pairs = [(g > b) + 0.5 * (g == b) for g in good for b in bad]
    return sum(pairs) / len(pairs) if pairs else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--good", type=int, default=15)
    ap.add_argument("--bad", type=int, default=15)
    args = ap.parse_args()
    if not DATA.exists():
        print(f"missing {DATA.relative_to(ROOT)}: run scripts/build_drafts_golden.py first", file=sys.stderr)
        return 1
    rows = [json.loads(line) for line in DATA.read_text().splitlines() if line.strip()]
    rng = random.Random(11)
    good = [r for r in rows if r["label"] == "good"][: args.good]
    bad_all = [r for r in rows if r["label"] == "bad"]
    # spread the bad sample across damage kinds
    by_kind: dict[str, list[dict]] = {}
    for r in bad_all:
        by_kind.setdefault(r["damage"], []).append(r)
    bad: list[dict] = []
    while len(bad) < min(args.bad, len(bad_all)):
        for kind in sorted(by_kind):
            if by_kind[kind] and len(bad) < args.bad:
                bad.append(by_kind[kind].pop(rng.randrange(len(by_kind[kind]))))

    llm = make_llm()
    scores: dict[str, list[float]] = {"good": [], "bad": []}
    per_kind: dict[str, list[float]] = {}
    paused = None
    for r in good + bad:
        d = DraftOut(channel="email", subject=r["subject"], body=r["body"], connection_note="", claims=[])
        try:
            s = judge(llm, d, r["post"]).value
        except QuotaExhausted as e:
            paused = str(e)
            break
        mean = (s.relevance + s.specificity + s.tone + s.honesty) / 4
        scores[r["label"]].append(mean)
        if r["damage"]:
            per_kind.setdefault(r["damage"], []).append(mean)

    value = auc(scores["good"], scores["bad"])
    report = {"auc": round(value, 3), "gate": GATE, "passed": value >= GATE and not paused,
              "judge_model": llm.model_for("judge"),  # type: ignore[arg-type]
              "n_good": len(scores["good"]), "n_bad": len(scores["bad"]),
              "mean_good": round(sum(scores["good"]) / max(1, len(scores["good"])), 2),
              "mean_bad_by_kind": {k: round(sum(v) / len(v), 2) for k, v in sorted(per_kind.items())}}
    if paused:
        report |= {"incomplete": True, "paused": paused}
    print(json.dumps(report, indent=2))
    return 2 if paused else 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
