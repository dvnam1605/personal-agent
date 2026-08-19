"""Unit tests for SQLAlchemy ORM models and relationships."""

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    ApprovalRequest,
    AssistantRun,
    AuditEvent,
    AuditOutbox,
    Conversation,
    Document,
    DocumentChunk,
    Entity,
    LLMExecution,
    Memory,
    Message,
    Skill,
    ToolExecution,
    User,
    WorkflowRun,
)


@pytest.fixture
async def async_session():
    """Create in-memory SQLite async engine and session for model testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_user_and_run_persistence(async_session: AsyncSession) -> None:
    """Verify User and AssistantRun creation with foreign key relationship."""
    user = User(
        email="test_user@example.com",
        full_name="Test User",
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()

    assert user.id is not None
    assert user.created_at is not None

    run = AssistantRun(
        user_id=user.id,
        correlation_id="corr_12345",
        langsmith_trace_id="trace_12345",
        request="Schedule a meeting with Nam",
        route_type="direct_specialist",
        domains=["calendar"],
        complexity="direct",
        status="running",
    )
    async_session.add(run)
    await async_session.flush()

    assert run.id is not None
    assert run.user_id == user.id
    assert run.correlation_id == "corr_12345"
    assert run.domains == ["calendar"]
    assert run.estimated_cost_usd == Decimal("0.000000")


@pytest.mark.asyncio
async def test_conversation_and_messages(async_session: AsyncSession) -> None:
    """Verify Conversation and Message hierarchy."""
    user = User(email="conv_user@example.com")
    async_session.add(user)
    await async_session.flush()

    conv = Conversation(user_id=user.id, title="Project Discussion")
    async_session.add(conv)
    await async_session.flush()

    msg1 = Message(
        conversation_id=conv.id,
        role="user",
        content="What is on my schedule today?",
    )
    msg2 = Message(
        conversation_id=conv.id,
        role="assistant",
        content="You have a sync meeting at 10 AM.",
    )
    async_session.add_all([msg1, msg2])
    await async_session.flush()

    assert msg1.id is not None
    assert msg2.conversation_id == conv.id


@pytest.mark.asyncio
async def test_audit_records_and_executions(async_session: AsyncSession) -> None:
    """Verify ToolExecution, LLMExecution, AuditEvent, and durable AuditOutbox models."""
    user = User(email="audit_user@example.com")
    async_session.add(user)
    await async_session.flush()

    run = AssistantRun(
        user_id=user.id,
        correlation_id="corr_99",
        request="Check inbox",
        route_type="direct_specialist",
        domains=["communication"],
        complexity="direct",
    )
    async_session.add(run)
    await async_session.flush()

    tool_exec = ToolExecution(
        run_id=run.id,
        tool_name="gmail.list_messages",
        input_parameters={"query": "is:unread"},
        output_summary={"count": 5},
        success=True,
        latency_ms=120.5,
    )
    llm_exec = LLMExecution(
        run_id=run.id,
        provider="google",
        model="gemini-1.5-pro",
        purpose="triage",
        prompt_tokens=150,
        completion_tokens=50,
        total_tokens=200,
        estimated_cost_usd=Decimal("0.000400"),
        latency_ms=350.0,
        success=True,
    )
    audit_evt = AuditEvent(
        run_id=run.id,
        user_id=user.id,
        event_type="policy_check",
        component="policy_engine",
        severity="INFO",
        payload={"action": "allowed"},
    )
    outbox_evt = AuditOutbox(
        run_id=run.id,
        event_kind="tool_execution",
        payload={"tool_name": "gmail.list_messages"},
    )

    async_session.add_all([tool_exec, llm_exec, audit_evt, outbox_evt])
    await async_session.flush()

    assert tool_exec.id is not None
    assert llm_exec.id is not None
    assert audit_evt.id is not None
    assert outbox_evt.status == "pending"
    assert llm_exec.estimated_cost_usd == Decimal("0.000400")


@pytest.mark.asyncio
async def test_future_phase_tables_structure(async_session: AsyncSession) -> None:
    """Verify Entity, Memory, Document, Skill, ApprovalRequest, and WorkflowRun tables with vector metadata."""
    user = User(email="future_user@example.com")
    async_session.add(user)
    await async_session.flush()

    run = AssistantRun(
        user_id=user.id,
        correlation_id="corr_future",
        request="test future",
        route_type="known_workflow",
        domains=["general"],
        complexity="direct",
    )
    async_session.add(run)
    await async_session.flush()

    entity = Entity(
        user_id=user.id,
        entity_type="person",
        canonical_name="Nam Nguyen",
        aliases=["Nam", "nam.nguyen"],
    )
    memory = Memory(
        user_id=user.id,
        memory_type="preference",
        content="Prefers 30-min meetings",
        embedding_model="text-embedding-3-large",
        embedding_dimensions=1536,
    )
    doc = Document(
        user_id=user.id,
        source_type="file_upload",
        title="architecture.pdf",
        metadata_={"access_token": "sk-document-secret", "nested": {"ok": True}},
    )
    async_session.add_all([entity, memory, doc])
    await async_session.flush()

    chunk = DocumentChunk(
        document_id=doc.id,
        chunk_index=0,
        hierarchy_level=1,
        node_type="section",
        heading_path=["Architecture"],
        content_raw="Architecture design overview...",
        content_embedding_text="Architecture design overview...",
        embedding_model="text-embedding-3-large",
        embedding_dimensions=1536,
    )
    approval = ApprovalRequest(
        run_id=run.id,
        action_type="send_email",
        description="Send email to Nam",
        target="nam@example.com",
        important_arguments={"subject": "Sync"},
        risk_level="HIGH_IMPACT_WRITE",
        expires_at=None,
    )
    skill = Skill(
        name="email_triage_v1",
        version="1.0.0",
        category="communication",
        description="Triage emails",
        definition_json={"steps": [1, 2]},
    )
    wf_run = WorkflowRun(
        run_id=run.id,
        workflow_name="meeting_prep",
        workflow_version="1.0.0",
        status="completed",
        executed_node_ids=["node_1", "node_2"],
    )
    async_session.add_all([chunk, approval, skill, wf_run])
    await async_session.flush()

    assert entity.id is not None
    assert memory.id is not None
    assert memory.embedding_model == "text-embedding-3-large"
    assert doc.id is not None
    assert doc.metadata_["access_token"] == "[REDACTED_SECRET]"
    assert doc.logical_document_id is not None
    assert chunk.id is not None
    assert chunk.node_type == "section"
    assert approval.id is not None
    assert approval.status == "pending"
    assert skill.id is not None
    assert skill.version == "1.0.0"
    assert wf_run.id is not None
