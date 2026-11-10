"""Anthropic implementation of the LLM interface (Python SDK 2.x)."""
from __future__ import annotations

import json
from typing import Any

import anthropic

from ..config import Settings
from .base import LLMResult, T, Tier, ToolSpec, Usage


def _usage(model: str, u: Any) -> Usage:
    return Usage(
        model=model,
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        calls=1,
    )


class AnthropicLLM:
    def __init__(self, settings: Settings) -> None:
        # Zero-arg resolution also picks up `ant auth login` profiles; pass the key only if set.
        self.client = (
            anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
            if settings.anthropic_api_key
            else anthropic.Anthropic(max_retries=3)
        )
        self.models = {
            Tier.SMALL: settings.signal_model_small,
            Tier.LARGE: settings.signal_model_large,
            Tier.JUDGE: settings.signal_model_judge,
        }

    def model_for(self, tier: Tier) -> str:
        return self.models[tier]

    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[T], effort: str = "low", max_tokens: int = 4000
    ) -> LLMResult[T]:
        model = self.model_for(tier)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_format": schema,
            "output_config": {"effort": effort},
        }
        resp = self.client.messages.parse(**kwargs)
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model refused ({model})")
        if resp.parsed_output is None:
            raise ValueError(f"no structured output (stop_reason={resp.stop_reason})")
        return LLMResult(value=resp.parsed_output, usage=_usage(model, resp.usage))

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
        """Manual tool loop with a hard turn budget, then one structured call that writes the result.

        The loop gathers notes with tools; the final call turns the transcript into `schema`. Keeping the
        output step separate means a run that hits its budget still returns a (partial) typed result.
        """
        model = self.model_for(tier)
        by_name = {t.name: t for t in tools}
        tool_defs = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        usage = Usage(model=model)
        transcript: list[dict[str, Any]] = []
        for _turn in range(max_turns):
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": 8000,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "tools": tool_defs,
                "messages": messages,
                "output_config": {"effort": effort},
            }
            resp = self.client.messages.create(**kwargs)
            usage.add(_usage(model, resp.usage))
            messages.append({"role": "assistant", "content": resp.content})
            calls = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                break
            results = []
            for call in calls:
                tool = by_name.get(call.name)
                try:
                    out = tool.fn(dict(call.input)) if tool else f"unknown tool {call.name}"
                    err = tool is None
                except Exception as e:  # tool errors go back to the model, they don't crash the run
                    out, err = f"error: {e}", True
                transcript.append({"tool": call.name, "input": dict(call.input), "output": out[:2000], "error": err})
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": out, "is_error": err})
            messages.append({"role": "user", "content": results})

        notes = json.dumps(transcript, ensure_ascii=False)[:60000]
        final = self.structured(
            tier=tier,
            system=system,
            user=f"{user}\n\nResearch notes (tool calls and results):\n{notes}\n\n"
            "Write the final result from these notes only. Mark it incomplete if the notes are thin.",
            schema=schema,
            effort="low",
        )
        usage.add(final.usage)
        return LLMResult(value=final.value, usage=usage, transcript=transcript)
