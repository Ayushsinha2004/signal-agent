"""Pick the provider and fill in per-provider model defaults. CLI and evals build their LLM here."""
from __future__ import annotations

from ..config import Settings, get_settings
from .base import LLM

DEFAULTS = {
    # judge differs from the drafter in both (ADR-005)
    "anthropic": {"small": "claude-haiku-5-5", "large": "claude-opus-5-5", "judge": "claude-sonnet-5-5"},
    # free tier (ADR-012): structured-output model for the many small calls, the largest free model for drafts,
    # a different family for the judge
    "openrouter": {"small": "nvidia/nemotron-3.5-super-vl:free", "large": "nvidia/nemotron-3-ultra-550b-a55b:free",
                   "judge": "xingchen-agi/xing4.0-29b-a4b:free"},
}


# OpenRouter routes to the next model in the same request when a free provider is busy (upstream 429s are
# common). The judge's list avoids the drafter's model so nothing grades its own output.
FALLBACKS = {
    "small": ["xingchen-agi/xing4.0-29b-a4b:free", "google/gemma-4-31b-it:free"],
    "large": ["google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-super-vl:free"],
    # Gemma 4 31B was the first judge but its upstream was rate-limited 3 times on 2026-11-10
    "judge": ["google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free"],
}


def provider(s: Settings) -> str:
    if s.signal_llm_provider != "auto":
        return s.signal_llm_provider
    return "anthropic" if s.anthropic_api_key else "openrouter"


def resolved(s: Settings | None = None) -> Settings:
    s = (s or get_settings()).model_copy()
    d = DEFAULTS[provider(s)]
    s.signal_model_small = s.signal_model_small or d["small"]
    s.signal_model_large = s.signal_model_large or d["large"]
    s.signal_model_judge = s.signal_model_judge or d["judge"]
    return s


def make_llm(s: Settings | None = None) -> LLM:
    s = resolved(s)
    inner: LLM
    if provider(s) == "anthropic":
        from .anthropic_llm import AnthropicLLM

        inner = AnthropicLLM(s)
    else:
        from .openrouter_llm import OpenRouterLLM

        inner = OpenRouterLLM(s)
    if s.signal_llm_cache:
        from .cache import CachingLLM

        return CachingLLM(inner)
    return inner
