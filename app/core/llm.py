"""Single non-streaming chat call to the LLM gateway for short structured tasks.

Every caller has a deterministic fallback, so a gateway failure returns None
instead of raising.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.http import llm_http_timeout
from app.core.jsonutil import extract_json_object

logger = logging.getLogger(__name__)


async def llm_text(
    system: str, user: str, *, timeout: float, temperature: float = 0.2
) -> str | None:
    api_key = str(settings.llm.openai_api_key or "")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=llm_http_timeout(timeout)) as client:
            resp = await client.post(
                settings.llm.chat_completions_url(),
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": settings.llm.primary_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    # 9router answers with SSE unless told otherwise.
                    "stream": False,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"].get("content") or ""
    except Exception as exc:  # noqa: BLE001 - callers fall back deterministically
        logger.warning("document_llm_call_failed error=%s", exc)
        return None
    return content.strip() or None


async def llm_json(
    system: str, user: str, *, timeout: float, temperature: float = 0.2
) -> dict[str, Any] | None:
    content = await llm_text(system, user, timeout=timeout, temperature=temperature)
    if content is None:
        return None
    try:
        return extract_json_object(content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_llm_json_invalid error=%s", exc)
        return None
