"""First-party PolicyEngine for fail-closed mutation governance (spec P18-01).

Enforces per-session approval policies, delegation pinning, risk-level classification,
and stale action target validation without framework dependencies (spec §18A.5).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ActionClass, ActionRiskLevel, ApprovalOutcome, ApprovalPolicy
from app.domain.models import DelegationContext, ProposedAction


class PolicyDecision(BaseModel):
    """Result of evaluating a proposed action against the PolicyEngine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool = Field(
        ...,
        description="Whether the action is authorized to execute immediately.",
    )
    needs_approval: bool = Field(
        default=False,
        description="Whether the action is blocked waiting for human or orchestration approval.",
    )
    reason: str = Field(
        default="",
        description="Explanation for the policy determination.",
    )
    outcome: ApprovalOutcome | None = Field(
        default=None,
        description="Explicit outcome classification if resolved (allowed-once, rejected, etc.).",
    )
    action_class: ActionClass | None = Field(
        default=None,
        description="Canonical action class assessed by the policy engine.",
    )
    risk_level: ActionRiskLevel | None = Field(
        default=None,
        description="Assessed risk tier of the evaluated action.",
    )


class PolicyEngine:
    """First-party policy authority evaluating mutation actions and session contracts."""

    @staticmethod
    def evaluate_action(
        action: ProposedAction,
        *,
        session_policy: ApprovalPolicy | str = ApprovalPolicy.ASK,
        delegation: DelegationContext | None = None,
        target_state: Any | None = None,
    ) -> PolicyDecision:
        """Evaluate an action against security boundaries, delegation pins, and session policy.

        Strict fail-closed contract:
        1. Read-only / non-approval actions are auto-allowed.
        2. Delegated specialists with approval_policy=NEVER cannot self-approve;
           they are auto-denied and marked needs_approval=True for the orchestrator.
        3. Headless/unattended sessions (approval_policy=NEVER) reject mutations deterministically
           without hanging the graph.
        4. Interactive sessions (approval_policy=ASK) halt execution awaiting human confirmation.
        """
        # 1. Non-mutation actions do not require approval
        if not action.requires_approval or action.risk_level == ActionRiskLevel.READ_ONLY:
            return PolicyDecision(
                allowed=True,
                needs_approval=False,
                reason="Action is read-only or explicitly exempted from approval.",
                outcome=None,
                risk_level=action.risk_level,
            )

        # 2. Delegation policy pinning (P18-01A, §17.1)
        # Delegated specialists cannot self-approve. Policy is captured and frozen at delegation time.
        if delegation is not None:
            child_policy = (
                delegation.approval_policy.value
                if isinstance(delegation.approval_policy, ApprovalPolicy)
                else str(delegation.approval_policy).upper()
            )
            if child_policy == "NEVER":
                return PolicyDecision(
                    allowed=False,
                    needs_approval=True,
                    reason=(
                        f"Delegated specialist '{delegation.target_agent}' cannot self-approve "
                        "mutations; approval_policy is pinned to NEVER."
                    ),
                    outcome=ApprovalOutcome.REJECTED,
                    risk_level=action.risk_level,
                )

        # 3. Session approval policy (interactive vs unattended)
        raw_policy = (
            session_policy.value
            if isinstance(session_policy, ApprovalPolicy)
            else str(session_policy).lower()
        )
        if raw_policy in ("ask", "policy", "always"):
            resolved_policy = ApprovalPolicy.ASK
        elif raw_policy == "never":
            resolved_policy = ApprovalPolicy.NEVER
        else:
            return PolicyDecision(
                allowed=False,
                needs_approval=False,
                reason="Unrecognized or invalid approval policy; fail-closed rejection.",
                outcome=ApprovalOutcome.REJECTED,
                risk_level=action.risk_level,
            )

        if resolved_policy is ApprovalPolicy.NEVER:
            # Deterministic auto-reject in headless/unattended mode: NEVER hangs the graph.
            return PolicyDecision(
                allowed=False,
                needs_approval=False,
                reason="Session approval_policy is NEVER; unattended mutations are rejected deterministically.",
                outcome=ApprovalOutcome.REJECTED,
                risk_level=action.risk_level,
            )

        # 4. Interactive session (ApprovalPolicy.ASK)
        return PolicyDecision(
            allowed=False,
            needs_approval=True,
            reason="Action requires human confirmation under interactive approval policy.",
            outcome=None,
            risk_level=action.risk_level,
        )

    @staticmethod
    def validate_target_state(
        action: ProposedAction,
        current_fingerprint: str | None,
        expected_fingerprint: str | None,
    ) -> bool:
        """Revalidate target state before executing an approved mutation (spec P18-05, H3).

        For tools that require resource fingerprinting, missing fingerprints fail closed.
        When expected_fingerprint is present, current_fingerprint must match exactly.
        """
        from app.services.approval_tokens import STALE_CHECK_FINGERPRINT_TOOLS

        if action.tool_name in STALE_CHECK_FINGERPRINT_TOOLS:
            if expected_fingerprint is None or current_fingerprint is None:
                return False
        if expected_fingerprint is not None:
            if current_fingerprint != expected_fingerprint:
                return False
        return True


__all__ = ["PolicyDecision", "PolicyEngine"]
