"""Default LLM callbacks for answer synthesis.

``PromptAnswerSynthesizer`` takes ``generate`` / ``stream_generate`` callbacks;
when none are injected it uses these, which call the configured OpenAI-compatible
gateway. A stream cut off after partial output raises ``SynthesisInterruptedError``
so the caller never passes a truncated answer off as complete.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable

import httpx

from app.core.config import settings
from app.core.http import llm_http_timeout

logger = logging.getLogger(__name__)


class SynthesisInterruptedError(Exception):
    """The LLM stream broke after producing part of the answer."""

    def __init__(self, partial: str) -> None:
        super().__init__("LLM stream interrupted after partial output")
        self.partial = partial


GenerateCallback = Callable[[str, str], Awaitable[str]]
StreamGenerateCallback = Callable[[str, str], AsyncGenerator[str, None]]


def build_default_generate() -> GenerateCallback | None:
    """The configured LLM gateway as a generate callback, or None without an API key."""
    try:
        api_key = settings.llm.openai_api_key
        if not api_key:
            return None

        async def _openai_generate(system: str, user: str) -> str:
            target_url = settings.llm.chat_completions_url()
            logger.info(
                "synthesis_calling_llm url=%s model=%s mode=%s",
                target_url,
                settings.llm.primary_model,
                str(settings.llm.mode),
            )
            timeout = settings.timeouts.llm_request_seconds
            chunks: list[str] = []
            stream_status: int | None = None
            # Use streaming under the hood so intermediate chunks keep proxy connection alive
            # and prevent 20s empty-body timeouts from upstream reverse proxies.
            # httpx timeouts are per read, so a slow-trickling stream also
            # gets an overall deadline.
            try:
                async with (
                    asyncio.timeout(timeout),
                    httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client,
                ):
                    async with client.stream(
                        "POST",
                        target_url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": settings.llm.primary_model,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            "temperature": settings.llm.temperature,
                            "stream": True,
                        },
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line or not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                payload = json.loads(data_str)
                                choices = payload.get("choices") or []
                                if choices:
                                    delta = choices[0].get("delta", {}).get("content")
                                    if delta:
                                        chunks.append(delta)
                            except Exception:  # noqa: BLE001 - malformed chunk should not abort stream
                                continue
            except Exception as stream_err:  # noqa: BLE001 - stream failure falls back to standard POST
                logger.warning("synthesis_stream_accumulation_failed error=%s", stream_err)
                if isinstance(stream_err, httpx.HTTPStatusError):
                    stream_status = stream_err.response.status_code
                partial = "".join(chunks).strip()
                if partial:
                    # Never pass a cut-off answer off as a complete one.
                    raise SynthesisInterruptedError(partial) from stream_err
                if isinstance(stream_err, (httpx.ConnectError, httpx.ConnectTimeout)):
                    # Gateway unreachable: a second POST would just wait again.
                    raise ValueError("LLM gateway unreachable") from stream_err

            content = "".join(chunks).strip()
            if not content and stream_status == 429:
                # Rate-limited gateway: an immediate second POST only burns quota.
                raise ValueError("LLM gateway rate-limited the synthesis request (429)")
            if not content:
                # Fallback to standard POST if streaming produced no chunks
                async with httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client:
                    resp = await client.post(
                        target_url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": settings.llm.primary_model,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            "temperature": settings.llm.temperature,
                        },
                    )
                    resp.raise_for_status()
                    if resp.content:
                        data = resp.json()
                        content = data["choices"][0]["message"].get("content") or ""

            if not content or not str(content).strip():
                raise ValueError(f"LLM returned empty content (model={settings.llm.primary_model})")
            return str(content)

        return _openai_generate
    except (ImportError, OSError, TimeoutError, TypeError, ValueError, KeyError):
        logger.exception("synthesis_default_generate_unavailable")
        return None


def build_default_stream_generate() -> StreamGenerateCallback | None:
    """The configured LLM gateway as a token-stream callback, or None without an API key."""
    try:
        api_key = settings.llm.openai_api_key
        if not api_key:
            return None

        async def _openai_stream_generate(system: str, user: str) -> AsyncGenerator[str, None]:
            target_url = settings.llm.chat_completions_url()
            logger.info(
                "synthesis_calling_llm_stream url=%s model=%s mode=%s",
                target_url,
                settings.llm.primary_model,
                str(settings.llm.mode),
            )
            timeout = settings.timeouts.llm_request_seconds
            async with httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client:
                async with client.stream(
                    "POST",
                    target_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": settings.llm.primary_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "temperature": settings.llm.temperature,
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            payload = json.loads(data_str)
                            choices = payload.get("choices") or []
                            if choices:
                                delta = choices[0].get("delta", {}).get("content")
                                if delta:
                                    yield delta
                        except Exception:  # noqa: BLE001 - malformed chunk should not abort stream
                            continue

        return _openai_stream_generate
    except (ImportError, OSError, TimeoutError, TypeError, ValueError, KeyError):
        logger.exception("synthesis_default_stream_generate_unavailable")
        return None


async def unconfigured_generate(system: str, user: str) -> str:
    """Fallback when no LLM key or generate callback is configured."""
    logger.warning(
        "synthesis_unconfigured_generate_invoked",
        extra={"hint": "inject a real LLM generate callback in production"},
    )
    return (
        "Answer synthesis is not yet configured.  "
        "Please provide a generate callback to PromptAnswerSynthesizer."
    )


async def unconfigured_stream_generate(system: str, user: str) -> AsyncGenerator[str, None]:
    """Fallback generator when no LLM key or stream generate callback is configured."""
    logger.warning(
        "synthesis_unconfigured_stream_generate_invoked",
        extra={"hint": "inject a real LLM stream generate callback in production"},
    )
    yield "Answer synthesis is not yet configured. Please provide a generate callback to PromptAnswerSynthesizer."
