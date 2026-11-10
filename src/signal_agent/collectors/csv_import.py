"""Import posts the user exported themselves (e.g. a Sales Navigator / Evaboot CSV, or a manual sheet).

Expected columns (case-insensitive): text (required), url, author, posted_at, id.
"""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from pathlib import Path

from ..models import RawPost


def collect(path: Path) -> list[RawPost]:
    posts: list[RawPost] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            r = {k.lower().strip(): (v or "").strip() for k, v in row.items() if k}
            if not r.get("text"):
                continue
            ext = r.get("id") or hashlib.sha1((r.get("url", "") + r["text"]).encode()).hexdigest()[:16]
            posted = None
            if r.get("posted_at"):
                try:
                    posted = datetime.fromisoformat(r["posted_at"])
                except ValueError:
                    posted = None
            posts.append(RawPost(source="csv", external_id=ext, url=r.get("url") or None,
                                 author=r.get("author") or None, posted_at=posted, text=r["text"]))
    return posts
