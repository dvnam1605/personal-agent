"""Repeat-tool guard with reminder injection and circuit breaker (spec P11-09).

Tracks consecutive calls of the same tool with identical normalized arguments
that keep failing or returning empty observations:

- after ``remind_after`` repeats, every subsequent LLM prompt is prefixed with
  a system reminder naming the tool, the failure count, and the last error;
- after ``breaker_after`` repeats, the circuit trips and the runner must stop
  (mapped to ``StopReason.NO_PROGRESS``) instead of burning more tokens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _is_empty_output(output: Any) -> bool:
    """Treat None/blank/empty-container outputs as empty observations."""
    if output is None:
        return True
    if isinstance(output, str):
        return not output.strip()
    if isinstance(output, (list, tuple, set, frozenset, dict)):
        return len(output) == 0
    return False


def normalize_arguments(arguments: dict[str, Any]) -> str:
    """Canonical key for repeat detection (sorted JSON, compact separators)."""
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class GuardDecision:
    """Outcome of observing one tool result."""

    reminder: str | None = None
    tripped: bool = False
    repeat_count: int = 0


@dataclass
class RepeatToolGuard:
    """Stateful consecutive-repeat detector (one instance per activation)."""

    remind_after: int = 2
    breaker_after: int = 3
    _last_key: str | None = field(default=None, init=False, repr=False)
    _repeat_count: int = field(default=0, init=False, repr=False)
    _tripped: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.remind_after < 1:
            raise ValueError("remind_after must be >= 1")
        if self.breaker_after < self.remind_after:
            raise ValueError("breaker_after must be >= remind_after")

    @property
    def tripped(self) -> bool:
        """Whether the circuit breaker has tripped."""
        return self._tripped

    def observe(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        success: bool,
        output: Any = None,
        error: str | None = None,
    ) -> GuardDecision:
        """Record one tool result; same failing/empty (tool, args) builds a streak."""
        key = f"{tool_name}\n{normalize_arguments(arguments)}"
        unproductive = (not success) or _is_empty_output(output)
        if unproductive and key == self._last_key:
            self._repeat_count += 1
        elif unproductive:
            self._last_key = key
            self._repeat_count = 1
        else:
            self._last_key = None
            self._repeat_count = 0
            return GuardDecision()

        if self._repeat_count >= self.breaker_after:
            self._tripped = True
            return GuardDecision(tripped=True, repeat_count=self._repeat_count)

        if self._repeat_count >= self.remind_after:
            detail = (error or "empty result").strip() or "empty result"
            reminder = (
                f"Tool '{tool_name}' failed {self._repeat_count} times with "
                f"identical arguments ({detail}). Stop retrying with the same "
                "parameters; change strategy or report the blocker."
            )
            return GuardDecision(reminder=reminder, repeat_count=self._repeat_count)
        return GuardDecision(repeat_count=self._repeat_count)
