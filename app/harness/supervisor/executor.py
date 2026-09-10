"""Delegation-backed supervisor task executor (spec P16 / P11-07).

The Supervisor must not run specialists itself or fabricate evidence. Every
task goes through :class:`DelegationService`, which pins ``approval_policy=NEVER``.
"""

from __future__ import annotations

from typing import Any

from app.agents.specialist.delegation import DelegationService
from app.domain.enums import EvidenceType, SpecialistStatus
from app.domain.models.retrieval.evidence import EvidenceItem, EvidenceSource
from app.domain.models.supervisor.specialist import DelegationRequest
from app.harness.supervisor.channels import TaskDispatchChannel


def build_delegation_task_executor(
    delegation: DelegationService,
    *,
    parent_agent: str,
) -> Any:
    """Return a LangGraph Send executor that delegates to a specialist."""

    async def _execute(payload: TaskDispatchChannel) -> dict[str, Any]:
        task_id = payload["task_id"]
        depth = int(payload.get("depth") or 1)
        request = DelegationRequest(
            parent_agent=parent_agent,
            parent_depth=max(depth - 1, 0),
            target_agent=payload["assigned_agent"],
            goal=payload.get("description") or payload.get("query") or task_id,
            context_data={
                **dict(payload.get("context_data") or {}),
                **dict(payload.get("input_data") or {}),
                "run_id": payload.get("run_id"),
                "user_id": payload.get("user_id"),
            },
            tool_restriction=payload.get("tool_restriction"),
        )
        result = await delegation.delegate(request)
        report_status = (result.status or "").strip().lower()
        if report_status == SpecialistStatus.NEEDS_MORE_CONTEXT.value:
            status = SpecialistStatus.NEEDS_MORE_CONTEXT.value
        elif result.needs_approval or report_status == SpecialistStatus.NEEDS_APPROVAL.value:
            status = SpecialistStatus.NEEDS_APPROVAL.value
        elif result.success or report_status == SpecialistStatus.SUCCESS.value:
            status = "completed"
        else:
            status = "failed"

        evidence_items: list[EvidenceItem] = []
        if result.success and result.output and result.output.strip():
            evidence_items.append(
                EvidenceItem(
                    id=f"ev_{task_id}",
                    evidence_type=EvidenceType.SYSTEM_FACT,
                    content=result.output.strip(),
                    source=EvidenceSource(
                        source_type="specialist_run",
                        source_id=task_id,
                        title=f"Result from {payload['assigned_agent']}",
                    ),
                )
            )
        missing = list(result.missing_context or [])
        out: dict[str, Any] = {
            "task_results": {
                task_id: {
                    "output": result.output,
                    "status": status,
                    "success": result.success,
                    "missing_context": missing,
                }
            },
            "completed_task_ids": [task_id] if status == "completed" else [],
            "evidence": evidence_items,
        }
        if status == SpecialistStatus.NEEDS_MORE_CONTEXT.value:
            out["missing_context"] = missing or [f"Task {task_id} needs more context"]
        if result.needs_approval or status == SpecialistStatus.NEEDS_APPROVAL.value:
            out["needs_approval"] = [{"task_id": task_id, "agent": result.agent_name}]
        if status == "failed":
            out["branch_errors"] = [result.output or f"Task {task_id} failed"]
        out["usage"] = result.usage.model_dump()
        return out

    return _execute
