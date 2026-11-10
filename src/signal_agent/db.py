"""Thin psycopg 3 helpers plus a migration runner for hand-written SQL files."""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import REPO_ROOT, get_settings

MIGRATIONS_DIR = REPO_ROOT / "migrations"


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    conn = psycopg.connect(url or get_settings().signal_database_url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def jsonb(value: Any) -> Jsonb:
    """Wrap pydantic models / dicts for jsonb columns."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return Jsonb(json.loads(json.dumps(value, default=str)))


def migrate(url: str | None = None) -> list[str]:
    applied: list[str] = []
    with connect(url) as conn:
        conn.execute(
            "create table if not exists schema_migrations (name text primary key, applied_at timestamptz default now())"
        )
        done = {r["name"] for r in conn.execute("select name from schema_migrations").fetchall()}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            applied.append(path.name)
    return applied
