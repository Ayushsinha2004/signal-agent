"""Provider-agnostic LLM interface. Pipeline code depends on this, never on a vendor SDK."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class QuotaExhausted(Exception):
    """The provider's rate or daily limit is used up. Runs pause on this; the lead is not marked FAILED."""


class ProviderBusy(RuntimeError):
    """A provider is temporarily overloaded (upstream 429). Fails one call, not the whole run."""


class Tier(StrEnum):
    SMALL = "small"   # normalise, classify, rerank
    LARGE = "large"   # research, draft
    JUDGE = "judge"   # verify (must differ from the drafter's model)


@dataclass
class Usage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0

    def add(self, other: Usage) -> None:
        self.model = other.model or self.model
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.calls += other.calls


@dataclass
class LLMResult[T]:
    value: T
    usage: Usage
    transcript: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[[dict[str, Any]], str]


class LLM(Protocol):
    def model_for(self, tier: Tier) -> str: ...

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[T], effort: str = "low", max_tokens: int = 4000
    ) -> LLMResult[T]: ...

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
    ) -> LLMResult[T]: ...
