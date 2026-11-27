"""Switch between the agentic (LangGraph) and rule-based multi-domain paths."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgenticSettings(BaseModel):
    """How multi-domain requests are planned and executed.

    ``agentic`` lets an LLM planner build a task DAG run by ReAct specialists over
    live read-only tools; ``rules`` keeps the fixed keyword pipeline. With
    ``fallback_to_rules`` a failed agentic run is answered by the rule pipeline
    instead of an error — never after an approval request was created.
    """

    supervisor_mode: Literal["agentic", "rules"] = Field(
        default="agentic",
        description="Planner for multi-domain requests: 'agentic' (LLM DAG) or 'rules'.",
    )
    fallback_to_rules: bool = Field(
        default=True,
        description="Answer with the rule pipeline when an agentic run fails or times out.",
    )
    dag_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        description="Wall-clock limit for one agentic run, including synthesis.",
    )
    observation_chars: int = Field(
        default=2000,
        ge=200,
        description="Longest tool result a specialist sees per call.",
    )
    specialist_max_total_tokens: int = Field(
        default=80_000,
        ge=1_000,
        description="Token budget of one specialist task (every turn resends the tool schemas).",
    )
