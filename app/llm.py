"""Token streaming from your own OpenAI-compatible gemma endpoint."""
import json
from collections.abc import AsyncIterator

import httpx

from .config import settings


async def complete(messages: list[dict], max_tokens: int, temperature: float = 0.0) -> str:
    """Non-streaming one-shot completion (for short helper tasks, e.g. query translation)."""
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    payload = {
        "model": settings.llm_model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.translate_timeout,
                                                       connect=10.0),
                                 trust_env=False) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


async def stream_completion(messages: list[dict]) -> AsyncIterator[str]:
    """messages: [{role, content}]. Yields the generated text pieces (deltas)."""
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    payload = {
        "model": settings.llm_model,
        "stream": True,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "messages": messages,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0),
                                 trust_env=False) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    delta = obj["choices"][0]["delta"].get("content")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta
