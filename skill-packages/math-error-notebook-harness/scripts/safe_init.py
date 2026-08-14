"""Protocol-safe DeepSeek API wrapper for the math notebook.

Derived from deepseek-harness 0.2.0.  Keep provider quirks here so task
adapters never call the OpenAI-compatible endpoint directly.
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterator

from openai import OpenAI


OFFICIAL_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", OFFICIAL_DEEPSEEK_BASE_URL)
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEFAULT_MAX_TOKENS = 4096
MAX_MESSAGE_CHARACTERS = 500_000
MAX_OUTPUT_TOKENS = 65_536
THINKING_ENABLED = {"thinking": {"type": "enabled"}}
THINKING_DISABLED = {"thinking": {"type": "disabled"}}

_client_instance: OpenAI | None = None


def _client() -> OpenAI:
    global _client_instance
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    if DEEPSEEK_BASE_URL.rstrip("/") != OFFICIAL_DEEPSEEK_BASE_URL:
        raise RuntimeError(
            "this Harness authorizes question data only to the official DeepSeek endpoint"
        )
    if _client_instance is None:
        _client_instance = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
    return _client_instance


def validate_history(messages: list[dict]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if (
            message.get("role") == "assistant"
            and message.get("tool_calls")
            and index + 1 < len(messages)
            and messages[index + 1].get("role") == "tool"
            and not message.get("reasoning_content")
        ):
            issues.append({
                "index": index,
                "rule": "C2",
                "message": "tool-call assistant missing reasoning_content",
            })
        if message.get("role") not in ("system", "user", "assistant", "tool"):
            issues.append({
                "index": index,
                "rule": "schema",
                "message": f"unknown role '{message.get('role')}'",
            })
    return {"ok": not issues, "violations": issues}


def _validate_request(
    messages: list[dict], max_tokens: int, enable_thinking: bool
) -> None:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    if max_tokens < 1 or max_tokens > MAX_OUTPUT_TOKENS:
        raise ValueError(f"max_tokens must be between 1 and {MAX_OUTPUT_TOKENS}")
    characters = len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")))
    if characters > MAX_MESSAGE_CHARACTERS:
        raise ValueError(
            f"message payload is too large ({characters} characters); split the batch"
        )
    if enable_thinking:
        history = validate_history(messages)
        if not history["ok"]:
            raise ValueError(
                "invalid thinking-mode history: "
                + json.dumps(history["violations"], ensure_ascii=False)
            )


def safe_deepseek_call(
    *,
    messages: list[dict],
    model: str = "deepseek-v4-pro",
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    enable_thinking: bool = False,
    max_tokens: int | None = None,
    temperature: float | None = None,
    stream: bool = False,
    **extra: Any,
) -> Any:
    """Call DeepSeek while preserving its reasoning and streaming contract."""
    if tools and DEEPSEEK_BASE_URL.rstrip("/").endswith("/beta"):
        raise ValueError("DeepSeek tool calls must not use the /beta endpoint")
    if max_tokens is None:
        max_tokens = DEFAULT_MAX_TOKENS
    _validate_request(messages, max_tokens, enable_thinking)
    extra_body = THINKING_ENABLED if enable_thinking else THINKING_DISABLED
    extra_body = {**extra_body, **(extra.pop("extra_body", None) or {})}

    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "extra_body": extra_body,
        **extra,
    }
    if tools is not None:
        create_kwargs["tools"] = tools
    if tool_choice is not None:
        create_kwargs["tool_choice"] = tool_choice
    if temperature is not None:
        create_kwargs["temperature"] = temperature
    if stream:
        create_kwargs["stream"] = True
        create_kwargs["stream_options"] = {"include_usage": True}
        return _stream_iter(create_kwargs)

    response = _client().chat.completions.create(**create_kwargs)
    message = response.choices[0].message
    output: dict[str, Any] = {"role": "assistant", "content": message.content}
    if message.tool_calls:
        output["tool_calls"] = [
            {
                "id": call.id,
                "type": call.type,
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in message.tool_calls
        ]
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        output["reasoning_content"] = reasoning
    output["_dsk_finish_reason"] = response.choices[0].finish_reason
    output["_dsk_usage"] = _normalize_usage(getattr(response, "usage", None))
    return output


def _stream_iter(create_kwargs: dict[str, Any]) -> Iterator[dict[str, Any]]:
    stream = _client().chat.completions.create(**create_kwargs)
    content_buffer: list[str] = []
    reasoning_buffer: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    usage_raw: Any | None = None

    for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            if getattr(chunk, "usage", None) is not None:
                usage_raw = chunk.usage
            continue
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        if reasoning := getattr(delta, "reasoning_content", None):
            reasoning_buffer.append(reasoning)
            yield {"type": "reasoning_delta", "data": reasoning}
        if content := getattr(delta, "content", None):
            content_buffer.append(content)
            yield {"type": "content_delta", "data": content}
        for call in getattr(delta, "tool_calls", None) or []:
            index = getattr(call, "index", 0)
            slot = tool_calls.setdefault(
                index, {"id": None, "name": None, "arguments": ""}
            )
            if getattr(call, "id", None):
                slot["id"] = call.id
            function = getattr(call, "function", None)
            if function is not None:
                if getattr(function, "name", None):
                    slot["name"] = function.name
                if getattr(function, "arguments", None):
                    slot["arguments"] += function.arguments
            yield {"type": "tool_call_delta", "data": {"index": index, **slot}}
        if reason := getattr(choice, "finish_reason", None):
            finish_reason = reason

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_buffer) or None,
    }
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": value["id"] or f"call_{index}",
                "type": "function",
                "function": {
                    "name": value["name"] or "",
                    "arguments": value["arguments"] or "{}",
                },
            }
            for index, value in sorted(tool_calls.items())
        ]
    if reasoning_buffer:
        message["reasoning_content"] = "".join(reasoning_buffer)
    yield {
        "type": "done",
        "message": message,
        "finish_reason": finish_reason,
        "usage": _normalize_usage(usage_raw),
    }


def _normalize_usage(usage_raw: Any) -> dict[str, Any]:
    if usage_raw is None:
        return {}
    usage = (
        usage_raw.model_dump()
        if hasattr(usage_raw, "model_dump")
        else dict(usage_raw)
    )
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cache_hit = usage.get("prompt_cache_hit_tokens")
    details = usage.get("prompt_tokens_details") or {}
    openai_cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if cache_hit is None and openai_cached is not None:
        cache_hit = int(openai_cached)
    if openai_cached is None and cache_hit is not None:
        openai_cached = int(cache_hit)
    cache_hit = int(cache_hit or 0)
    openai_cached = int(openai_cached or 0)
    cache_miss = int(
        usage.get("prompt_cache_miss_tokens") or max(prompt - cache_hit, 0)
    )
    usage["prompt_cache_hit_tokens"] = cache_hit
    usage["prompt_cache_miss_tokens"] = cache_miss
    usage["prompt_tokens_details"] = {"cached_tokens": openai_cached}
    if prompt:
        usage["cache_hit_rate"] = round(cache_hit / prompt, 4)
        usage["estimated_cost_usd"] = round(
            cache_miss / 1_000_000 * 0.14
            + cache_hit / 1_000_000 * 0.0028
            + completion / 1_000_000 * 0.28,
            8,
        )
    return usage


def prepare_for_new_user_turn(history: list[dict]) -> list[dict]:
    return [
        (
            {key: value for key, value in message.items() if key != "reasoning_content"}
            if message.get("role") == "assistant"
            else dict(message)
        )
        for message in history
    ]
