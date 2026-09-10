"""Unit tests for fail-closed skill activation and graph-candidate telemetry."""

import pytest

from app.agents.specialist.react import SpecialistRunner
from app.domain.enums import ActionClass, ActionRiskLevel, SpecialistStatus, StopReason
from app.domain.errors import PermissionDeniedError
from app.domain.models import (
    ReActTraceStep,
    SkillConstraints,
    SkillDefinition,
    SkillMetadata,
    SkillStep,
    SpecialistOutcome,
    SpecialistReport,
    SpecialistTrace,
    ToolDefinition,
)
from app.services.approvals import generate_approval_token
from app.services.skills.execution import (
    SkillExecutor,
    authorize_skill_step,
    intersect_agent_view_with_skill,
)
from app.services.skills.loader import parse_skill_directory
from app.services.skills.registry import DEFAULT_SKILLS_DIR
from app.services.skills.telemetry import HARDENING_MIN_TRACES, SkillTelemetry
from app.tools.registry import ToolRegistryView


def _tool(
    name: str,
    *,
    capabilities: list[str],
    is_mutation: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        category=name.split(".", 1)[0],
        capabilities=capabilities,
        is_mutation=is_mutation,
        action_class=ActionClass.SAFE_WRITE if is_mutation else ActionClass.READ,
        risk_level=(ActionRiskLevel.LOW_IMPACT_WRITE if is_mutation else ActionRiskLevel.READ_ONLY),
    )


READ_THREAD = _tool("gmail.get_thread", capabilities=["gmail.read"])
SEARCH_MESSAGES = _tool("gmail.search_messages", capabilities=["gmail.read", "gmail.search"])
CREATE_DRAFT = _tool(
    "gmail.create_draft",
    capabilities=["gmail.drafts", "gmail.write"],
    is_mutation=True,
)
RETRIEVE = _tool("retrieval.retrieve", capabilities=["retrieval.read"])
SYNTHESIZE = _tool("retrieval.synthesize", capabilities=["retrieval.read", "retrieval.synthesize"])
SEARCH_EVENTS = _tool("calendar.search_events", capabilities=["calendar.read", "calendar.search"])
GET_EVENT = _tool("calendar.get_event", capabilities=["calendar.read", "calendar.events"])
SEARCH_DRIVE = _tool("drive.search_files", capabilities=["drive.read", "drive.search"])


def _follow_up_skill() -> SkillDefinition:
    return parse_skill_directory(DEFAULT_SKILLS_DIR / "email-follow-up")


def _meeting_prep_skill() -> SkillDefinition:
    return parse_skill_directory(DEFAULT_SKILLS_DIR / "meeting-prep")


def _full_view(*, read_only: bool = False) -> ToolRegistryView:
    return ToolRegistryView(
        [
            READ_THREAD,
            SEARCH_MESSAGES,
            CREATE_DRAFT,
            RETRIEVE,
            SYNTHESIZE,
            SEARCH_EVENTS,
            GET_EVENT,
            SEARCH_DRIVE,
        ],
        is_read_only=read_only,
    )


def _outcome(
    *,
    tools: tuple[str, ...],
    needs_approval: bool = False,
) -> SpecialistOutcome:
    status = SpecialistStatus.NEEDS_APPROVAL if needs_approval else SpecialistStatus.SUCCESS
    stop = StopReason.POLICY if needs_approval else StopReason.SUCCESS
    steps = [
        ReActTraceStep(
            iteration=index,
            tool=name,
            arguments={},
            observation_summary="blocked" if needs_approval and name.endswith("draft") else "ok",
            success=not (needs_approval and name.endswith("draft")),
        )
        for index, name in enumerate(tools, start=1)
    ]
    return SpecialistOutcome(
        agent_name="CommunicationAgent",
        report=SpecialistReport(status=status, summary="Done."),
        trace=SpecialistTrace(steps=steps, stop_reason=stop),
        needs_approval=needs_approval,
    )


def test_mutation_skill_cannot_activate_in_read_only_view() -> None:
    executor = SkillExecutor()
    with pytest.raises(PermissionDeniedError, match="read-only"):
        executor.activate(
            _follow_up_skill(),
            agent_name="CommunicationAgent",
            goal="Draft a follow-up",
            available_capabilities={"gmail.read", "retrieval.read", "gmail.drafts"},
            agent_view=_full_view(read_only=True),
            read_only=True,
            approval_token="tok-1",
            permit_mutations=True,
        )


def test_mutation_skill_activates_read_path_without_approval_token() -> None:
    executor = SkillExecutor()
    activation = executor.activate(
        _follow_up_skill(),
        agent_name="CommunicationAgent",
        goal="Draft a follow-up",
        available_capabilities={"gmail.read", "retrieval.read", "gmail.drafts"},
        agent_view=_full_view(),
        permit_mutations=True,
    )
    assert "gmail.get_thread" in activation.tool_view
    assert "retrieval.retrieve" in activation.tool_view
    assert "gmail.create_draft" in activation.tool_view
    assert activation.task.permit_mutations is False
    assert activation.task.approval_token is None
    assert executor.telemetry.stats("email-follow-up", "1.0.0").activations == 0


def test_mutation_step_cannot_execute_without_approval_token() -> None:
    skill = _follow_up_skill()
    draft_step = skill.steps[3]
    view = ToolRegistryView([READ_THREAD, CREATE_DRAFT], is_read_only=False)
    with pytest.raises(PermissionDeniedError, match="approval token"):
        authorize_skill_step(
            skill,
            draft_step,
            view,
            read_only=False,
            approval_token=None,
        )


def test_mutation_step_cannot_execute_in_read_only_intersected_view() -> None:
    skill = _follow_up_skill()
    view = intersect_agent_view_with_skill(_full_view(), skill, read_only=True)
    assert "gmail.create_draft" not in view
    with pytest.raises(PermissionDeniedError, match="read-only"):
        authorize_skill_step(
            skill,
            skill.steps[3],
            view,
            read_only=True,
            approval_token="tok-1",
        )


def test_approved_mutation_skill_keeps_draft_tool_and_guidance() -> None:
    executor = SkillExecutor()
    token = generate_approval_token(
        "skill-appr-1", tool_name="gmail.create_draft", run_id="run-skill"
    )
    activation = executor.activate(
        _follow_up_skill(),
        agent_name="CommunicationAgent",
        goal="Draft a follow-up",
        available_capabilities={"gmail.read", "retrieval.read", "gmail.drafts"},
        agent_view=_full_view(),
        approval_token=token,
        permit_mutations=True,
        run_id="run-skill",
    )
    assert "gmail.create_draft" in activation.tool_view
    assert activation.task.permit_mutations is True
    assert activation.task.approval_token == token
    assert any("email-follow-up" in line for line in activation.guidance_preamble)
    assert activation.task.system_preamble == activation.guidance_preamble


def test_read_only_meeting_prep_strips_mutations() -> None:
    executor = SkillExecutor()
    activation = executor.activate(
        _meeting_prep_skill(),
        agent_name="KnowledgeResearchAgent",
        goal="Prepare the board meeting brief",
        available_capabilities={
            "calendar.read",
            "gmail.read",
            "drive.read",
            "retrieval.read",
        },
        agent_view=_full_view(),
        read_only=True,
    )
    assert "gmail.create_draft" not in activation.tool_view
    assert activation.tool_view.read_only is True
    assert activation.task.permit_mutations is False


def test_intersection_drops_tools_outside_required_capabilities() -> None:
    skill = SkillDefinition(
        metadata=SkillMetadata(
            name="gmail-only-skill",
            version="1.0.0",
            description="Only Gmail read tools should survive intersection.",
            required_capabilities=["gmail.read"],
        ),
        steps=[
            SkillStep(
                step_index=1, name="Read", description="Read mail.", tool_name="gmail.get_thread"
            )
        ],
        completion_criteria="Mail was read.",
    )
    view = intersect_agent_view_with_skill(_full_view(), skill, read_only=True)
    assert "gmail.get_thread" in view
    assert "calendar.search_events" not in view


def test_missing_capabilities_fail_closed() -> None:
    executor = SkillExecutor()
    with pytest.raises(PermissionDeniedError, match="does not hold"):
        executor.activate(
            _meeting_prep_skill(),
            agent_name="CommunicationAgent",
            goal="Prepare the brief",
            available_capabilities={"gmail.read"},
            agent_view=_full_view(read_only=True),
            read_only=True,
        )


def test_mutation_skill_without_permit_mutations_still_activates_reads() -> None:
    activation = SkillExecutor().activate(
        _follow_up_skill(),
        agent_name="CommunicationAgent",
        goal="Draft a follow-up",
        available_capabilities={"gmail.read", "retrieval.read", "gmail.drafts"},
        agent_view=_full_view(),
        approval_token="tok-1",
        permit_mutations=False,
    )
    assert activation.task.permit_mutations is False
    assert activation.task.approval_token is None
    assert "gmail.create_draft" in activation.tool_view


def test_mutation_skill_missing_draft_tool_fails_closed_on_that_step() -> None:
    with pytest.raises(PermissionDeniedError, match="intersected view"):
        SkillExecutor().activate(
            _follow_up_skill(),
            agent_name="CommunicationAgent",
            goal="Draft a follow-up",
            available_capabilities={"gmail.read", "retrieval.read", "gmail.drafts"},
            agent_view=ToolRegistryView([READ_THREAD, RETRIEVE], is_read_only=False),
        )


def test_authorize_missing_tool_outside_read_only_view() -> None:
    skill = _follow_up_skill()
    view = ToolRegistryView([READ_THREAD], is_read_only=False)
    with pytest.raises(PermissionDeniedError, match="intersected view"):
        authorize_skill_step(
            skill,
            skill.steps[3],
            view,
            read_only=False,
            approval_token="tok-1",
        )


def test_authorize_mutation_tool_rejects_read_only_even_if_visible() -> None:
    skill = _follow_up_skill()
    view = ToolRegistryView([CREATE_DRAFT], is_read_only=False)
    with pytest.raises(PermissionDeniedError, match="read-only"):
        authorize_skill_step(
            skill,
            skill.steps[3],
            view,
            read_only=True,
            approval_token="tok-1",
        )


def test_reasoning_only_skill_uses_deny_all_restriction_when_view_is_empty() -> None:
    skill = SkillDefinition(
        metadata=SkillMetadata(
            name="think-skill",
            version="1.0.0",
            description="Reasoning-only procedure with no bound tools.",
        ),
        steps=[SkillStep(step_index=1, name="Think", description="Do not call tools.")],
        completion_criteria="A plan is written.",
    )
    activation = SkillExecutor().activate(
        skill,
        agent_name="CommunicationAgent",
        goal="Think",
        available_capabilities=set(),
        agent_view=ToolRegistryView([], is_read_only=True),
        read_only=True,
    )
    assert activation.task.tool_restriction is not None
    assert activation.task.tool_restriction.deny == ["*"]


def test_graph_candidate_stats_require_observed_stable_volume() -> None:
    telemetry = SkillTelemetry()
    executor = SkillExecutor(telemetry=telemetry)
    skill = SkillDefinition(
        metadata=SkillMetadata(
            name="stable-skill",
            version="1.0.0",
            description="Tiny skill used to exercise hardening statistics.",
            required_capabilities=["gmail.read"],
            triggers=["stable skill"],
        ),
        steps=[SkillStep(step_index=1, name="Read", description="Read mail.")],
        completion_criteria="Done.",
        constraints=SkillConstraints(max_steps=2),
    )
    view = ToolRegistryView([READ_THREAD], is_read_only=True)
    executor.activate(
        skill,
        agent_name="CommunicationAgent",
        goal="Run it",
        available_capabilities={"gmail.read"},
        agent_view=view,
        read_only=True,
    )
    assert telemetry.stats("stable-skill", "1.0.0").activations == 0

    success = _outcome(tools=("gmail.get_thread",))
    for _ in range(HARDENING_MIN_TRACES):
        executor.observe_run(skill, success)
    stats = telemetry.stats("stable-skill", "1.0.0")
    assert stats.activations == HARDENING_MIN_TRACES
    assert stats.completions == HARDENING_MIN_TRACES
    assert stats.unique_step_sequences == 1
    assert stats.ready_for_hardening is True

    blocked = SkillTelemetry()
    blocked.record_outcome(
        "stable-skill",
        "1.0.0",
        _outcome(tools=("gmail.get_thread", "gmail.create_draft"), needs_approval=True),
    )
    blocked_stats = blocked.stats("stable-skill", "1.0.0")
    assert blocked_stats.activations == 1
    assert blocked_stats.completions == 0
    assert blocked_stats.ready_for_hardening is False

    early = SkillTelemetry()
    early.record_run("stable-skill", "1.0.0", ("gmail.get_thread",), completed=True)
    assert early.stats("stable-skill", "1.0.0").ready_for_hardening is False
    assert SkillTelemetry().stats("missing", "1.0.0").activations == 0


async def test_follow_up_reads_then_needs_approval_and_restricts_wide_view() -> None:
    """H1/H2/M1: read steps run, mutation asks for approval, runner enforces restriction."""
    from tests.unit.agents._fakes import (
        DictExecutor,
        ScriptedChat,
        calls_turn,
        make_agent,
        ok_result,
    )

    skill = _follow_up_skill()
    executor = SkillExecutor()
    activation = executor.activate(
        skill,
        agent_name="CommunicationAgent",
        goal="Summarize the thread and draft a reply",
        available_capabilities={"gmail.read", "retrieval.read", "gmail.drafts"},
        agent_view=_full_view(),
    )
    chat = ScriptedChat(
        [
            calls_turn(("calendar.search_events", {"q": "secret"})),
            calls_turn(("gmail.get_thread", {"thread_id": "t1"})),
            calls_turn(("gmail.create_draft", {"body": "draft"})),
        ]
    )
    tool_exec = DictExecutor(
        {
            "calendar.search_events": lambda args: ok_result("calendar.search_events"),
            "gmail.get_thread": lambda args: ok_result("gmail.get_thread", "thread"),
            "gmail.create_draft": lambda args: ok_result("gmail.create_draft", "draft"),
        }
    )
    outcome = await SpecialistRunner(chat, tool_exec).run(
        activation.task,
        make_agent(name="CommunicationAgent"),
        _full_view(),
        run_id="r1",
        user_id="u1",
    )
    executed = [name for name, _args in tool_exec.calls]
    assert "calendar.search_events" not in executed
    assert executed == ["gmail.get_thread"]
    assert "not available" in outcome.trace.steps[0].observation_summary
    assert outcome.needs_approval is True
    assert outcome.report.status is SpecialistStatus.NEEDS_APPROVAL
    executor.observe_run(skill, outcome)
    stats = executor.telemetry.stats("email-follow-up", "1.0.0")
    observed = tuple(step.tool for step in outcome.trace.steps)
    assert stats.activations == 1
    assert stats.completions == 0
    assert stats.ready_for_hardening is False
    assert "gmail.get_thread" in observed
    assert observed[-1] == "gmail.create_draft"
