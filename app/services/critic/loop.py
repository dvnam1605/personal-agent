"""Evaluator–optimizer loop: an independent critic reviews an output and asks for revisions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Critique:
    """A critic's verdict on one output: acceptable or not, and why."""

    ok: bool
    issues: list[str] = field(default_factory=list)


@dataclass
class ReviewedOutput(Generic[T]):
    """The final output and every critique it went through, oldest first."""

    output: T
    critiques: list[Critique]

    @property
    def accepted(self) -> bool:
        return bool(self.critiques) and self.critiques[-1].ok

    @property
    def revisions(self) -> int:
        return max(0, len(self.critiques) - 1)


class EvaluatorOptimizer(Generic[T]):
    """Critique → revise until the critic accepts or ``max_revisions`` is spent.

    The critic and the reviser are separate callables, so the critic can be cheaper,
    deterministic, or a different model from the writer, and the loop is reusable for
    any output type (an email draft, a plan, a synthesized answer). A reviser that
    returns ``None`` (no model available, no change) ends the loop with the last output.
    """

    def __init__(
        self,
        critique: Callable[[T], Awaitable[Critique]],
        revise: Callable[[T, Critique], Awaitable[T | None]],
        *,
        max_revisions: int = 2,
    ) -> None:
        if max_revisions < 0:
            raise ValueError("max_revisions must not be negative.")
        self._critique = critique
        self._revise = revise
        self._max_revisions = max_revisions

    async def run(self, initial: T) -> ReviewedOutput[T]:
        output = initial
        critiques: list[Critique] = []
        for attempt in range(self._max_revisions + 1):
            verdict = await self._critique(output)
            critiques.append(verdict)
            if verdict.ok or attempt == self._max_revisions:
                break
            revised = await self._revise(output, verdict)
            if revised is None or revised == output:
                break
            output = revised
        return ReviewedOutput(output=output, critiques=critiques)
