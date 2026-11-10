from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import USER_ENV, get_settings
from .models import Criteria, EvidenceItem


@dataclass
class Profile:
    id: str
    name: str
    criteria: Criteria
    evidence: list[EvidenceItem]
    facts: dict[str, Any]
    denylist: list[str]  # private words that must never appear in a draft (e.g. client names)

    def evidence_by_id(self) -> dict[str, EvidenceItem]:
        return {e.id: e for e in self.evidence}


def load_criteria(name: str | None = None, root: Path | None = None) -> Criteria:
    """Criteria alone. Evals and CI use this: `evidence.yaml` is private and not in the repo."""
    s = get_settings()
    d = (root or s.profile_dir.parent) / (name or s.signal_profile)
    return Criteria(**yaml.safe_load((d / "criteria.yaml").read_text()))


def load_profile(name: str | None = None, root: Path | None = None) -> Profile:
    s = get_settings()
    name = name or s.signal_profile
    d = (root or s.profile_dir.parent) / name
    ev_file = Path(s.signal_evidence_file) if s.signal_evidence_file and root is None else d / "evidence.yaml"
    ev = yaml.safe_load(ev_file.read_text())
    crit = Criteria(**yaml.safe_load((d / "criteria.yaml").read_text()))
    deny_file = Path(s.signal_denylist_file) if s.signal_denylist_file else USER_ENV.parent / f"denylist-{name}.txt"
    deny = [w.strip() for w in deny_file.read_text().splitlines() if w.strip()] if deny_file.exists() else []
    items = [EvidenceItem(id=e["id"], text=e["text"], metrics=e.get("metrics", []), skills=e.get("skills", []))
             for e in ev["evidence"]]
    return Profile(id=name, name=ev["name"], criteria=crit, evidence=items, facts=ev.get("facts", {}), denylist=deny)
