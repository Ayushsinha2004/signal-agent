"""Every stage execution becomes a step_runs row (cost, tokens, latency, error).

Langfuse export (observability.py) is optional and added on top of this; Postgres is the source of truth.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg

from .db import jsonb
from .llm import QuotaExhausted, Usage, cost_usd
from .observability import get_exporter


@dataclass
class StepRecord:
    usage: Usage = field(default_factory=Usage)
    detail: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"


@contextmanager
def step(conn: psycopg.Connection[Any], lead_id: int | None, stage: str) -> Iterator[StepRecord]:
    rec = StepRecord()
    t0 = time.monotonic()
    err: str | None = None
    exporter, obs = get_exporter(), None
    if exporter is not None and lead_id is not None:
        row = conn.execute("select trace_id from leads where id = %s", (lead_id,)).fetchone()
        if row:
            obs = exporter.start(row["trace_id"], stage, lead_id)
    try:
        yield rec
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:2000]
        rec.status = "quota" if isinstance(e, QuotaExhausted) else "error"
        raise
    finally:
        if err:
            conn.rollback()  # the failed statement poisoned the transaction; record the failure cleanly
        u = rec.usage
        cost = cost_usd(u.model, u.input_tokens, u.output_tokens, u.cache_read_tokens) if u.calls else 0.0
        conn.execute(
            """insert into step_runs (lead_id, stage, status, model, input_tokens, output_tokens, cache_read_tokens,
                                      cost_usd, latency_ms, error, detail, finished_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())""",
            (lead_id, stage, rec.status, u.model or None, u.input_tokens, u.output_tokens, u.cache_read_tokens,
             cost, int((time.monotonic() - t0) * 1000), err, jsonb(rec.detail)),
        )
        if lead_id is not None and cost:
            conn.execute("update leads set cost_usd = cost_usd + %s where id = %s", (cost, lead_id))
        conn.commit()
        if exporter is not None:
            exporter.finish(obs, model=u.model or None, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                            cache_read_tokens=u.cache_read_tokens, cost_usd=cost, detail=rec.detail, error=err)
