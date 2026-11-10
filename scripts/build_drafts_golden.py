"""Build the judge-calibration set (PLAN.md §5.3) from messages I wrote and approved by hand.

    uv run python scripts/build_drafts_golden.py ~/resume-system/out/ready-to-send

Good examples: every message (## section with a **Subject:**) in the Markdown files under the given folder.
Bad examples: the same messages damaged in ways a judge should notice: a generic template, the wrong company,
buzzword filler, a pushy tone, and rambling. Built in code, so no model writes its own test data.

Output goes to evals/data/private/drafts_golden.jsonl, which is gitignored: the messages name real people.
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "data" / "private" / "drafts_golden.jsonl"
ROLE = re.compile(r"(?i)\bfit \d|founder|\bceo\b|\bcto\b|head of|chief|director|manager")


def parse(md: str, source: str) -> list[dict]:
    items = []
    for sec in re.split(r"\n## ", md)[1:]:
        header, _, rest = sec.partition("\n")
        m = re.search(r"\*\*Subject:\*\*\s*(.+)", rest)
        if not m:
            continue
        context = rest[:m.start()].strip()
        body = rest[m.end():]
        body = re.split(r"\n\*\*Connection note:\*\*|\n---", body)[0].strip()
        # headers are "N. Person, Company", "N. Company (Country), fit 4/5" or "N. Company (role, ...)":
        # the company is the header part the message itself names
        parts = [p.strip() for p in re.sub(r"\s*\(.*?\)", "", re.sub(r"^\d+\.\s*", "", header)).split(",")]
        cands = [p for p in (parts[1:] if len(parts) > 1 else parts) if p and not ROLE.search(p)] or parts
        company = next((p for p in cands if p in body), cands[0])
        if len(body.split()) < 25:
            continue
        items.append({"source": source, "company": company, "header": header.strip(), "subject": m.group(1).strip(),
                      "body": body, "post": f"{header.strip()}\n{context}"[:2000]})
    return items


GENERIC = ("Hi there,\n\nI came across your company and was really impressed by what you are doing. I am a "
           "passionate and hardworking developer with strong skills in many technologies and I am always eager to "
           "learn. I believe I would be a great fit for any role on your team. Please let me know if there are any "
           "opportunities available.\n\nThanks,\nAyush")


def damage(item: dict, others: list[dict], rng: random.Random) -> list[dict]:
    body, co = item["body"], item["company"]
    wrong = rng.choice([o["company"] for o in others if o["company"] != co] or ["Acme"])
    buzz = ("I am a results-driven, synergy-focused innovator who leverages cutting-edge AI to deliver "
            "best-in-class, world-class, game-changing solutions that move the needle and unlock 10x value. ")
    pushy = (body + "\n\nI expect a reply by Friday. Honestly you would be making a mistake not to hire me, and I "
             "have other offers, so please decide quickly.")
    ramble = (body + "\n\n" + "Also, a bit more about me: I have always loved computers since I was young, and "
              "over the years I have tried many languages and frameworks, some of which I liked more than others. "
              * 3).strip()
    kinds = {
        "generic": GENERIC,
        "wrong_company": body.replace(co, wrong) if co and co in body else f"Hi, saw {wrong} is hiring. " + body,
        "buzzwords": buzz + body,
        "pushy": pushy,
        "rambling": ramble,
    }
    return [{**item, "body": b, "label": "bad", "damage": k} for k, b in kinds.items()]


def main() -> int:
    folder = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "resume-system/out/ready-to-send"
    good = []
    for md in sorted(folder.glob("*/*.md")):
        if md.name.lower() in ("readme.md", "research.md", "notes.md", "contacts.md", "application-answers.md",
                               "loom-script.md"):
            continue
        good += parse(md.read_text(), md.parent.name)
    rng = random.Random(7)  # fixed seed: the same set every time
    bad = [d for g in good for d in damage(g, good, rng)]
    rng.shuffle(bad)
    rows = [{**g, "label": "good", "damage": None} for g in good] + bad
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as fh:
        for i, r in enumerate(rows):
            fh.write(json.dumps({"id": i, **r}, ensure_ascii=False) + "\n")
    print(f"{len(good)} good, {len(bad)} bad -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
