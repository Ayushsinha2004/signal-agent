"""USD per million tokens (Anthropic first-party list prices, 2026-10). Update when prices change."""
from __future__ import annotations

PRICES: dict[str, tuple[float, float, float]] = {
    # model: (input, output, cache_read)
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0) -> float:
    if model.endswith(":free"):  # OpenRouter free models
        return 0.0
    # unknown model: price as Opus (conservative)
    pin, pout, pcache = PRICES.get(model.removeprefix("anthropic/"), (4.00, 20.00, 0.20))
    return (input_tokens * pin + output_tokens * pout + cache_read_tokens * pcache) / 1_000_000
