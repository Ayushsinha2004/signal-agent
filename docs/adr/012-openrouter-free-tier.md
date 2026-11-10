# ADR-012: Run on OpenRouter's free models, behind the same interface

Status: Accepted, 2026-11-10. Deviation from PLAN.md §16 (Claude models by default). Amends ADR-005.

## Context
The project has no paid model budget yet. OpenRouter's free models (`:free`) cost nothing but come with hard limits: 20 requests a minute and 50 a day on an account that hasn't bought credits. Only some free models accept JSON-schema output, and free providers are often busy (upstream 429s).

## Decision
- `llm/openrouter_llm.py` implements the same `LLM` protocol as the Anthropic class, over OpenRouter's chat-completions API with plain `httpx`. `llm/factory.py` picks the provider (`SIGNAL_LLM_PROVIDER`, default `auto`: Anthropic when its key is set, else OpenRouter) and fills in per-provider models.
- Free defaults: `nvidia/nemotron-3.5-super-vl:free` for the small tier (it supports structured output and tools), `nvidia/nemotron-3-ultra-550b-a55b:free` for drafting, and `google/gemma-4-31b-it:free` as the judge, a different family from the drafter. Each tier has a fallback list that OpenRouter tries within the same request when a provider is busy; the judge's list never contains the drafter.
- The JSON schema always goes in the prompt and, where supported, in `response_format`. Every reply is validated with Pydantic, with one repair attempt that shows the model its validation error.
- `QuotaExhausted` is raised on the daily cap (or a local `SIGNAL_MAX_REQUESTS` budget). The runner lets it through without marking the lead `FAILED`, the step is logged with status `quota`, and `signal run` stops cleanly. The next run continues from the same state.
- `llm/cache.py` caches every reply on disk (`data/cache/llm`, gitignored), keyed by model, prompts and schema. The eval and the real run share calls for the same post, and a paused run never pays twice. Cache hits record `calls = 0`.
- Free models are priced at $0 in `llm/pricing.py`.

## Consequences
- Running costs nothing, but a full HN thread takes several days of quota. `signal run` handles leads that are furthest along first, so each day's budget turns into finished drafts.
- Free models are slower (the 550B drafter took 66 s for one call) and weaker than Claude, so the p95 latency target (60 s per lead) is not met on this tier. Eval numbers are reported per model.
- Free providers may log prompts. Post text is public, but DRAFT sends evidence items. That's accepted for now; moving to a paid model is a config change (`SIGNAL_LLM_PROVIDER=anthropic` or a paid OpenRouter model id).
- Switching back to Claude needs no code change.
