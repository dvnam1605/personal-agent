"""In-memory graph-candidate telemetry for dynamic skills (ADR 0005)."""

from __future__ import annotations

from collections import Counter, defaultdict

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import SpecialistStatus
from app.domain.models import SpecialistOutcome

HARDENING_MIN_TRACES = 50
HARDENING_STABILITY_THRESHOLD = 0.90


class GraphCandidateStats(BaseModel):
    """Hardening-readiness snapshot for one skill version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_name: str
    version: str
    activations: int = Field(ge=0)
    completions: int = Field(ge=0)
    unique_step_sequences: int = Field(ge=0)
    dominant_sequence_ratio: float = Field(ge=0.0, le=1.0)
    ready_for_hardening: bool


class SkillTelemetry:
    """Record skill activations so later phases can decide graph hardening."""

    def __init__(self) -> None:
        self._sequences: dict[tuple[str, str], list[tuple[str, ...]]] = defaultdict(list)
        self._completions: dict[tuple[str, str], int] = defaultdict(int)

    def record_run(
        self,
        name: str,
        version: str,
        step_names: tuple[str, ...],
        *,
        completed: bool,
    ) -> None:
        """Store one observed tool-invocation fingerprint from a finished run."""
        key = (name, version)
        self._sequences[key].append(step_names)
        if completed:
            self._completions[key] += 1

    def record_outcome(
        self,
        name: str,
        version: str,
        outcome: SpecialistOutcome,
    ) -> None:
        """Record the actual ReAct tool path after ``SpecialistRunner.run()``."""
        completed = outcome.report.status is SpecialistStatus.SUCCESS and not outcome.needs_approval
        self.record_run(
            name,
            version,
            tuple(step.tool for step in outcome.trace.steps),
            completed=completed,
        )

    def stats(self, name: str, version: str) -> GraphCandidateStats:
        """Return ADR-0005 candidate statistics for ``name``@``version``."""
        key = (name, version)
        sequences = self._sequences.get(key, [])
        activations = len(sequences)
        if activations == 0:
            return GraphCandidateStats(
                skill_name=name,
                version=version,
                activations=0,
                completions=0,
                unique_step_sequences=0,
                dominant_sequence_ratio=0.0,
                ready_for_hardening=False,
            )
        counts = Counter(sequences)
        dominant = max(counts.values())
        ratio = dominant / activations
        return GraphCandidateStats(
            skill_name=name,
            version=version,
            activations=activations,
            completions=self._completions.get(key, 0),
            unique_step_sequences=len(counts),
            dominant_sequence_ratio=ratio,
            ready_for_hardening=(
                activations >= HARDENING_MIN_TRACES and ratio >= HARDENING_STABILITY_THRESHOLD
            ),
        )
