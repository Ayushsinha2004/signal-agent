"""Optional Langfuse export (ADR-006). Postgres `step_runs` stays the source of truth.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are both set and the `langfuse` extra is installed
(`uv sync --extra langfuse`). One Langfuse trace per lead (id = `leads.trace_id`), one observation per stage:
a generation when the stage called a model, a span otherwise. Export problems are logged and swallowed;
they never fail a pipeline step.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Protocol

import structlog

from .config import get_settings

log = structlog.get_logger()


class Observation(Protocol):
    def update(self, **kw: Any) -> Any: ...

    def end(self) -> Any: ...


class Exporter(Protocol):
    def start(self, trace_id: str, stage: str, lead_id: int | None) -> Observation | None: ...

    def finish(self, obs: Observation | None, *, model: str | None, input_tokens: int, output_tokens: int,
               cache_read_tokens: int, cost_usd: float, detail: dict[str, Any], error: str | None) -> None: ...


class LangfuseExporter:
    def __init__(self, client: Any) -> None:
        self.client = client

    def start(self, trace_id: str, stage: str, lead_id: int | None) -> Observation | None:
        try:
            obs: Observation = self.client.start_observation(
                trace_context={"trace_id": trace_id}, name=stage, as_type="generation",
                metadata={"lead_id": lead_id, "stage": stage})
            return obs
        except Exception as e:  # noqa: BLE001 - tracing must never break the pipeline
            log.warning("langfuse_start_failed", stage=stage, error=type(e).__name__)
            return None

    def finish(self, obs: Observation | None, *, model: str | None, input_tokens: int, output_tokens: int,
               cache_read_tokens: int, cost_usd: float, detail: dict[str, Any], error: str | None) -> None:
        if obs is None:
            return
        try:
            kw: dict[str, Any] = {"output": detail or None, "level": "ERROR" if error else "DEFAULT",
                                  "status_message": error}
            if model:
                kw |= {"model": model,
                       "usage_details": {"input": input_tokens, "output": output_tokens,
                                         "cache_read_input_tokens": cache_read_tokens},
                       "cost_details": {"total": cost_usd}}
            obs.update(**kw)
            obs.end()
        except Exception as e:  # noqa: BLE001
            log.warning("langfuse_finish_failed", error=type(e).__name__)

    def flush(self) -> None:
        try:
            self.client.flush()
        except Exception as e:  # noqa: BLE001
            log.warning("langfuse_flush_failed", error=type(e).__name__)


@lru_cache(maxsize=1)
def get_exporter() -> LangfuseExporter | None:
    s = get_settings()
    if not (s.langfuse_public_key and s.langfuse_secret_key):
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        log.warning("langfuse_keys_set_but_not_installed", hint="uv sync --extra langfuse")
        return None
    logging.getLogger("httpx").setLevel(logging.WARNING)  # HTTP client INFO logs can carry auth headers
    return LangfuseExporter(Langfuse(public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key,
                                     host=s.langfuse_host))


def flush() -> None:
    exp = get_exporter()
    if exp is not None:
        exp.flush()
