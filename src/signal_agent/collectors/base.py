from __future__ import annotations

from typing import Any

import psycopg

from ..models import RawPost


def save_posts(conn: psycopg.Connection[Any], posts: list[RawPost]) -> int:
    """Insert posts; (source, external_id) makes re-runs idempotent. Returns how many were new."""
    new = 0
    for p in posts:
        row = conn.execute(
            """insert into raw_posts (source, external_id, url, author, posted_at, text)
               values (%s, %s, %s, %s, %s, %s)
               on conflict (source, external_id) do nothing returning id""",
            (p.source, p.external_id, p.url, p.author, p.posted_at, p.text),
        ).fetchone()
        new += row is not None
    return new
