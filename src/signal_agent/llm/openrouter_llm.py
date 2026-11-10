"""OpenRouter implementation of the LLM interface (OpenAI-compatible chat completions over httpx).

Built for the free tier (`:free` models): 20 requests/minute and 50/day without credits. So it
- throttles itself under the per-minute limit,
- raises `QuotaExhausted` on the daily cap, which pauses a run instead of failing leads,
- asks for JSON-schema output where the model supports it, and validates every reply with Pydantic anyway,
  with one repair attempt that shows the model its validation error.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from ..config import Settings
from .base import LLMResult, ProviderBusy, QuotaExhausted, T, Tier, ToolSpec, Usage

API = "https://openrouter.ai/api/v1"
# Free models whose providers accept `response_format: json_schema` (OpenRouter /models, 2026-11-10).
STRUCTURED = {"nvidia/nemotron-3.5-super-vl:free", "dots-studio/dots-3-note-preview:free",
              "xingchen-agi/xing4.0-29b-a4b:free", "liquid/lfm-3-1.7b:free", "liquid/lfm-2.5-2.6b:free"}
JSON_MODE = {"google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free", "recursive/flint-v1.0:free"}
MAX_WAIT_S = 90  # wait out a per-minute 429 up to this long; anything longer is the daily cap
MIN_TOKENS = 8000  # free models reason before answering and the reasoning counts against max_tokens
MAX_TOKENS = 24000


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic emits $defs/$ref; several providers reject refs, so inline them."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out: dict[str, Any] = walk(schema)
    return out


def extract_json(text: str) -> str:
    """Models wrap JSON in fences or prose; take the outermost object."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


class OpenRouterLLM:
    def __init__(self, settings: Settings, client: httpx.Client | None = None, min_interval_s: float = 3.2,
                 busy_wait_s: float = 20.0) -> None:
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set (put it in ~/.config/signal-agent/env)")
        self.client = client or httpx.Client(base_url=API, timeout=180)
        self.headers = {"Authorization": f"Bearer {settings.openrouter_api_key}", "X-Title": "signal-agent"}
        self.models = {
            Tier.SMALL: settings.signal_model_small,
            Tier.LARGE: settings.signal_model_large,
            Tier.JUDGE: settings.signal_model_judge,
        }
        from .factory import FALLBACKS

        override = [m.strip() for m in settings.signal_model_fallbacks.split(",") if m.strip()]
        self.fallbacks: dict[str, list[str]] = {t.value: override or FALLBACKS[t.value] for t in Tier}
        self.min_interval_s = min_interval_s
        self.busy_wait_s = busy_wait_s
        self.max_requests = settings.signal_max_requests
        self.requests = 0
        self._last = 0.0

    def model_for(self, tier: Tier) -> str:
        return self.models[tier]

    # -- transport ---------------------------------------------------------------------------------------------
    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(3):
            if self.max_requests is not None and self.requests >= self.max_requests:
                raise QuotaExhausted(f"local request budget of {self.max_requests} used")
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            r = self.client.post("/chat/completions", json=body, headers=self.headers)
            if r.status_code == 429:
                reset = r.headers.get("x-ratelimit-reset")  # epoch ms; absent on upstream-provider 429s
                delay = max(0.0, int(reset) / 1000 - time.time()) if reset and reset.isdigit() else self.busy_wait_s
                if "upstream" in r.text:  # one busy provider, not our quota: fail this call only
                    if attempt >= 1:
                        raise ProviderBusy(f"{body['model']} is rate-limited upstream: {r.text[:160]}")
                elif delay > MAX_WAIT_S or attempt == 2:
                    raise QuotaExhausted(f"OpenRouter rate limit (resets in {int(delay)}s): {r.text[:200]}")
                time.sleep(delay + 1)
                continue
            if r.status_code in (502, 503) and attempt < 2:  # free providers drop requests now and then
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:300]}")
            data: dict[str, Any] = r.json()
            if "error" in data:  # errors can also arrive with HTTP 200
                code = data["error"].get("code")
                if code == 429 and "upstream" in str(data["error"]):
                    raise ProviderBusy(f"{body['model']} is rate-limited upstream")
                if code == 429:
                    raise QuotaExhausted(f"OpenRouter rate limit: {data['error'].get('message', '')[:200]}")
                raise RuntimeError(f"OpenRouter error {code}: {str(data['error'].get('message'))[:300]}")
            return data
        raise RuntimeError("unreachable")

    def _body(self, tier: Tier, messages: list[dict[str, Any]], max_tokens: int, effort: str,
              **extra: Any) -> dict[str, Any]:
        model = self.model_for(tier)
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens,
                                "temperature": 0.2, "reasoning": {"effort": effort, "exclude": True},
                                "usage": {"include": True}, **extra}
        fallbacks = [m for m in self.fallbacks.get(tier.value, []) if m != model]
        if fallbacks:
            body["models"] = [model, *fallbacks]
        return body

    @staticmethod
    def _usage(model: str, data: dict[str, Any]) -> Usage:
        u = data.get("usage") or {}
        return Usage(model=data.get("model") or model, input_tokens=u.get("prompt_tokens", 0) or 0,
                     output_tokens=u.get("completion_tokens", 0) or 0,
                     cache_read_tokens=(u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0, calls=1)

    # -- interface ---------------------------------------------------------------------------------------------
    def structured(
        self, *, tier: Tier, system: str, user: str, schema: type[T], effort: str = "low", max_tokens: int = 4000
    ) -> LLMResult[T]:
        model = self.model_for(tier)
        js = inline_refs(schema.model_json_schema())
        sys_msg = (f"{system}\n\nReply with exactly one JSON object and nothing else. It must validate against "
                   f"this JSON schema:\n{json.dumps(js)}")
        extra: dict[str, Any] = {}
        if model in STRUCTURED:
            extra["response_format"] = {"type": "json_schema",
                                        "json_schema": {"name": schema.__name__, "strict": False, "schema": js}}
        elif model in JSON_MODE:
            extra["response_format"] = {"type": "json_object"}
        messages: list[dict[str, Any]] = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]
        usage = Usage(model=model)
        last_err = ""
        budget = max(max_tokens, MIN_TOKENS)
        for _attempt in range(2):
            data = self._post(self._body(tier, messages, budget, effort, **extra))
            usage.add(self._usage(model, data))
            choice = data["choices"][0]
            content = (choice["message"].get("content") or "")
            if choice.get("finish_reason") == "length" or not content.strip():
                # reasoning used the whole budget before any JSON: a repair prompt can't fix that, more room can
                last_err = f"reply cut off at {budget} tokens (finish_reason={choice.get('finish_reason')})"
                budget = min(budget * 3, MAX_TOKENS)
                continue
            try:
                value = schema.model_validate_json(extract_json(content))
                return LLMResult(value=value, usage=usage)
            except ValidationError as e:
                last_err = str(e)[:1500]
                messages += [{"role": "assistant", "content": content[:6000]},
                             {"role": "user", "content": f"That JSON did not validate:\n{last_err}\n"
                                                         "Reply again with only the corrected JSON object."}]
        raise ValueError(f"no valid {schema.__name__} from {model} after a repair attempt: {last_err[:300]}")

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
        """Tool loop with a hard turn budget, then one structured call that writes the result (as in AnthropicLLM)."""
        model = self.model_for(tier)
        by_name = {t.name: t for t in tools}
        tool_defs = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                        "parameters": t.input_schema}} for t in tools]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{system}\n\nUse the tools to gather facts. Stop calling tools when "
                                          "you have enough, and reply with a short plain-text summary of findings."},
            {"role": "user", "content": user}]
        usage = Usage(model=model)
        transcript: list[dict[str, Any]] = []
        for _turn in range(max_turns):
            data = self._post(self._body(tier, messages, 3000, effort, tools=tool_defs))
            usage.add(self._usage(model, data))
            msg = data["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls}
                            if calls else {"role": "assistant", "content": msg.get("content") or ""})
            if not calls:
                if msg.get("content"):
                    transcript.append({"tool": "notes", "input": {}, "output": msg["content"][:2000], "error": False})
                break
            for call in calls:
                fn = call.get("function", {})
                tool = by_name.get(fn.get("name", ""))
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    out = tool.fn(args) if tool else f"unknown tool {fn.get('name')}"
                    err = tool is None
                except Exception as e:  # tool errors go back to the model, they don't crash the run
                    args, out, err = {}, f"error: {e}", True
                transcript.append({"tool": fn.get("name"), "input": args, "output": out[:2000], "error": err})
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": out})

        notes = json.dumps(transcript, ensure_ascii=False)[:40000]
        final = self.structured(
            tier=tier, system=system,
            user=f"{user}\n\nResearch notes (tool calls and results):\n{notes}\n\n"
                 "Write the final result from these notes only. Set complete to false if the notes are thin.",
            schema=schema, effort="low")
        usage.add(final.usage)
        return LLMResult(value=final.value, usage=usage, transcript=transcript)


_ = BaseModel  # schema types are pydantic models
