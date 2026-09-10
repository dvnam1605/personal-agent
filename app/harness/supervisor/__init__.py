"""Supervisor LangGraph substrate package (spec P16 / §18A.5 / ADR 0011).

Exports:
- SupervisorGraphBuilder: compiles the dynamic Supervisor DAG StateGraph.
- SupervisorChannels: typed channels carrying DAG state.
- TaskDispatchChannel: typed channel for Send API fan-out to specialists.
"""

from app.harness.supervisor.channels import SupervisorChannels, TaskDispatchChannel
from app.harness.supervisor.executor import build_delegation_task_executor
from app.harness.supervisor.graph import SupervisorGraphBuilder

__all__ = [
    "SupervisorChannels",
    "SupervisorGraphBuilder",
    "TaskDispatchChannel",
    "build_delegation_task_executor",
]
