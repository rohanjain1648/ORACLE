"""LLM client wrapper supporting Anthropic (primary) and OpenAI (ensemble)."""

from __future__ import annotations

import json
import re
from typing import Any

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential


_anthropic_client: anthropic.Anthropic | None = None
_openai_client: Any | None = None


def _get_anthropic(api_key: str) -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


def _get_openai(api_key: str) -> Any:
    global _openai_client
    if _openai_client is None:
        try:
            import openai
            _openai_client = openai.OpenAI(api_key=api_key)
        except ImportError:
            pass
    return _openai_client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_anthropic(
    prompt: str,
    system: str,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    temperature: float = 0.3,
    max_tokens: int = 1500,
    thinking_budget: int = 0,
) -> str:
    client = _get_anthropic(api_key)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    if thinking_budget > 0:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
        # Extended thinking requires temperature=1
        kwargs["temperature"] = 1
    else:
        kwargs["temperature"] = temperature

    response = client.messages.create(**kwargs)
    # Extract text from response (skip thinking blocks)
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_openai(
    prompt: str,
    system: str,
    api_key: str,
    model: str = "gpt-4o",
    temperature: float = 0.3,
    max_tokens: int = 1500,
) -> str:
    client = _get_openai(api_key)
    if client is None:
        raise RuntimeError("OpenAI client not available")
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""


def extract_json(text: str) -> dict | list:
    """Extract JSON object or array from LLM response text."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON block in markdown
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", text)
    if match:
        return json.loads(match.group(1))
    # Try to find raw JSON object/array
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"No JSON found in LLM response: {text[:200]}")
