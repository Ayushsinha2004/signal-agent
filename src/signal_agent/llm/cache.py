"""Disk cache in front of any LLM. Same model + prompt + schema = same reply, read from disk for free.

Why: on a 50-requests-a-day free tier, the eval and the real run see the same posts, and a run that pauses
on the daily cap must resume without paying again for the stages it already did. Cached replies come back
with `calls = 0`, so they cost nothing in step_runs and are easy to tell apart from fresh calls.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT
from .base import LLM, LLMResult, T, Tier, ToolSpec, Usage

CACHE_DIR = REPO_ROOT / "data" / "cache" / "llm"


class CachingLLM:
    def __init__(self, inner: LLM, root: Path = CACHE_DIR) -> None:
        self.inner = inner
        self.root = root
        self.hits = 0
        self.misses = 0

    def model_for(self, tier: Tier) -> str:
        return self.inner.model_for(tier)

    def _key(self, kind: str, **parts: Any) -> Path:
        h = hashlib.sha256(json.dumps({"kind": kind, **parts}, sort_keys=True, default=str).encode()).hexdigest()
        return self.root / h[:2] / f"{h}.json"

    def _get(self, path: Path, schema: type[T]) -> LLMResult[T] | None:
        if not path.exists():
            return None
        try:
            d = json.loads(path.read_text())
            value = schema.model_validate(d["value"])
        except (ValueError, KeyError):  # stale or corrupt entry: treat as a miss
            return None
        self.hits += 1
        return LLMResult(value=value, usage=Usage(model=d.get("model", ""), calls=0),
                         transcript=d.get("transcript", []))

    def _put(self, path: Path, res: LLMResult[Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"model": res.usage.model, "value": res.value.model_dump(mode="json"),
                                   "transcript": res.transcript}, ensure_ascii=False))
        tmp.replace(path)

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[T], effort: str = "low", max_tokens: int = 4000
    ) -> LLMResult[T]:
        path = self._key("structured", model=self.model_for(tier), system=system, user=user,
                         schema=schema.model_json_schema())
        hit = self._get(path, schema)
        if hit is not None:
            return hit
        self.misses += 1
        res = self.inner.structured(tier=tier, system=system, user=user, schema=schema, effort=effort,
                                    max_tokens=max_tokens)
        self._put(path, res)
        return res

    def agent(
        self,
        *,
        tier: Tier,
        system: str,
        user: str,
        tools: list[ToolSpec],
        schema: type[T],
        max_turns: int,
        effort: str = "medium",
    ) -> LLMResult[T]:
        path = self._key("agent", model=self.model_for(tier), system=system, user=user,
                         tools=[t.name for t in tools], max_turns=max_turns, schema=schema.model_json_schema())
        hit = self._get(path, schema)
        if hit is not None:
            return hit
        self.misses += 1
        res = self.inner.agent(tier=tier, system=system, user=user, tools=tools, schema=schema,
                               max_turns=max_turns, effort=effort)
        self._put(path, res)
        return res
