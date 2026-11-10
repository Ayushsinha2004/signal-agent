"""Build profiles/<name>/evidence.yaml from a private resume evidence bank (master.yaml).

Keeps only achievements without an open `verify` note. Text, metrics and skills only; proof paths and
private notes stay behind. Run: uv run python scripts/import_evidence.py ~/resume-system/evidence/master.yaml ayush
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

src, profile = Path(sys.argv[1]).expanduser(), sys.argv[2]
master = yaml.safe_load(src.read_text())
items = []
for group in ("experience", "projects"):
    for role in master.get(group, []) or []:
        org = role.get("company") or role.get("name") or role.get("id")
        for a in role.get("achievements", []) or []:
            if a.get("verify"):
                continue
            items.append({"id": a["id"], "org": org, "text": a["text"],
                          "metrics": [str(m) for m in a.get("metrics", []) or []],
                          "skills": a.get("skills", []) or []})
            for vname, vtext in (a.get("variants") or {}).items():
                items.append({"id": f"{a['id']}.{vname}", "org": org, "text": vtext,
                              "metrics": [str(m) for m in a.get("metrics", []) or []],
                              "skills": a.get("skills", []) or []})
facts = master.get("facts", {})
out = {"name": master["contact"]["name"], "facts": facts, "evidence": items}
dest = Path(__file__).resolve().parents[1] / "profiles" / profile / "evidence.yaml"
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=120))
print(f"wrote {len(items)} evidence items to {dest}")
