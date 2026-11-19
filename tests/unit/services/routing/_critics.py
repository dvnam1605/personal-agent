"""Critic doubles for orchestrator tests, so no test reaches a model gateway."""

from __future__ import annotations

from app.services.critic import Critique, EmailDraft, ReviewedOutput


class AcceptingCritic:
    """Accepts every draft unchanged; records what it reviewed."""

    def __init__(self) -> None:
        self.reviewed: list[EmailDraft] = []

    async def review(self, request: str, draft: EmailDraft) -> ReviewedOutput[EmailDraft]:
        self.reviewed.append(draft)
        return ReviewedOutput(output=draft, critiques=[Critique(ok=True)])
