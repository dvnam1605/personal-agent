"""Background memory consolidation worker (spec P17-07)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.services.context.entity_resolver import EntityResolver
from app.services.context.episodic_service import EpisodicMemoryService


class BackgroundConsolidationWorker:
    """Consolidates turn interactions into episodic and entity memory asynchronously without blocking user response."""

    def __init__(
        self,
        episodic_service: EpisodicMemoryService,
        entity_resolver: EntityResolver,
    ) -> None:
        self._episodic = episodic_service
        self._resolver = entity_resolver
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._running = False
        self._processed_count = 0

    def enqueue_turn(
        self,
        user_id: str,
        user_prompt: str,
        assistant_response: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Enqueue a completed conversational turn for non-blocking consolidation."""
        self._queue.put_nowait(
            {
                "user_id": user_id,
                "user_prompt": user_prompt,
                "assistant_response": assistant_response,
                "metadata": metadata or {},
            }
        )

    async def process_one(self) -> bool:
        """Process one pending item from the consolidation queue."""
        if self._queue.empty():
            return False

        item = await self._queue.get()
        try:
            user_id = item["user_id"]
            user_prompt = item["user_prompt"]
            assistant_response = item["assistant_response"]

            # 1. If turn contains milestone facts, record to episodic memory
            if not self._episodic.is_ephemeral_chatter(user_prompt):
                fact_summary = (
                    f"User: {user_prompt[:120]} -> Resolution: {assistant_response[:120]}"
                )
                await self._episodic.record_event(
                    user_id=user_id,
                    content=fact_summary,
                    importance=0.6,
                    metadata=item.get("metadata"),
                )

            self._processed_count += 1
            return True
        finally:
            self._queue.task_done()

    async def drain(self) -> int:
        """Process all queued consolidation tasks until queue is empty."""
        count = 0
        while not self._queue.empty():
            if await self.process_one():
                count += 1
        return count

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def processed_count(self) -> int:
        return self._processed_count
