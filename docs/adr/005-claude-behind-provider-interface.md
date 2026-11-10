# ADR-005: Claude behind a provider-agnostic interface

Status: Accepted, 2026-11-10. Tier assignment amended by [ADR-009](009-research-on-small-model.md); default provider by [ADR-012](012-openrouter-free-tier.md).

## Context
Different stages need different trade-offs between cost and quality, and a model shouldn't grade its own writing.

## Decision
Pipeline code depends only on the `LLM` protocol in `llm/base.py` (`structured()` and `agent()`), never on a vendor SDK. `llm/anthropic_llm.py` implements it with structured outputs and a manual tool loop. Models are picked by tier from env: `SIGNAL_MODEL_SMALL`, `SIGNAL_MODEL_LARGE`, `SIGNAL_MODEL_JUDGE`. The judge tier defaults to a different model from the drafter.

## Consequences
- Tests use a deterministic `FakeLLM`, so CI is free and repeatable; live-model checks run only in the eval job.
- System prompts are sent with `cache_control` so repeated calls read the prompt from cache.
