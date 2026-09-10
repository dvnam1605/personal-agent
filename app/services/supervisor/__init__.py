"""Supervisor services package (spec P16 / ADR 0011).

Provides capability catalog building, deterministic DAG validation,
structured planning, and continuable subagent session management.
Zero framework imports.
"""

from app.services.supervisor.catalog import build_capability_catalog
from app.services.supervisor.planner import SupervisorPlanner
from app.services.supervisor.session_manager import ContinuableSessionManager
from app.services.supervisor.validator import validate_execution_plan

__all__ = [
    "ContinuableSessionManager",
    "SupervisorPlanner",
    "build_capability_catalog",
    "validate_execution_plan",
]
