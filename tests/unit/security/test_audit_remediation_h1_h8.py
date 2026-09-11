"""Comprehensive regression test suite for V1 audit remediation (H1-H8, M1-M3, M6, M11)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.declarations import CALENDAR_AGENT
from app.core.config import Environment, Settings
from app.core.security import FernetTokenCipher, TokenEncryptionError
from app.domain.enums import ActionRiskLevel
from app.domain.errors import PermissionDeniedError
from app.domain.models import (
    ProposedAction,
    ToolContext,
    ToolInput,
    UserQuestionAnswer,
)
from app.infrastructure.db.models import ApprovalRequest as ApprovalRequestRow
from app.services.approvals import (
    STALE_CHECK_FINGERPRINT_TOOLS,
    generate_approval_token,
    require_mutation_approval,
    verify_approval_token,
)
from app.services.approvals.consumed_store import RedisConsumedTokenStore
from app.services.approvals.question_plane import QuestionPlaneService
from app.tools.ask_user import execute_ask_user


class TestH1FernetProductionKeyFile:
    """H1: Fernet auto-generate key must raise in staging/production if key file is missing."""

    def test_fernet_raises_in_production_when_key_file_missing(self, tmp_path) -> None:
        missing_file = tmp_path / "missing_google.key"
        with patch("app.core.config.get_settings") as mock_settings:
            mock_cfg = MagicMock()
            mock_cfg.environment = Environment.PRODUCTION
            mock_settings.return_value = mock_cfg

            with pytest.raises(TokenEncryptionError, match="Refusing to auto-generate"):
                FernetTokenCipher.from_key_file(missing_file)

    def test_fernet_auto_generates_in_development_when_missing(self, tmp_path) -> None:
        key_file = tmp_path / "dev_google.key"
        with patch("app.core.config.get_settings") as mock_settings:
            mock_cfg = MagicMock()
            mock_cfg.environment = Environment.DEVELOPMENT
            mock_settings.return_value = mock_cfg

            cipher = FernetTokenCipher.from_key_file(key_file)
            assert key_file.exists()
            assert cipher.encrypt("hello") != "hello"


class TestH2ApprovalTokenUserIdBinding:
    """H2: Approval tokens must bind user_id and enforce user identity match."""

    @pytest.mark.asyncio
    async def test_token_binds_and_verifies_user_id(self) -> None:
        token = generate_approval_token(
            approval_id="appr-123",
            tool_name="gmail.send_draft",
            run_id="run-456",
            user_id="alice",
        )
        # Matching user_id verifies
        assert await verify_approval_token(
            token,
            "gmail.send_draft",
            consume=False,
            expected_run_id="run-456",
            expected_user_id="alice",
        )
        # Mismatched user_id rejects
        assert not await verify_approval_token(
            token,
            "gmail.send_draft",
            consume=False,
            expected_run_id="run-456",
            expected_user_id="bob",
        )

    @pytest.mark.asyncio
    async def test_production_requires_user_id(self) -> None:
        with patch("app.services.approvals.tokens._is_relaxed_token_env", return_value=False):
            with pytest.raises(ValueError, match="user_id is required"):
                generate_approval_token(
                    approval_id="appr-123",
                    tool_name="gmail.send_draft",
                    run_id="run-456",
                    user_id=None,
                )


class TestH3StaleCheckFailClosed:
    """H3: Stale check must fail closed for tools in STALE_CHECK_FINGERPRINT_TOOLS."""

    @pytest.mark.asyncio
    async def test_stale_check_requires_expected_fingerprint_on_update(self) -> None:
        token = generate_approval_token(
            approval_id="appr-cal",
            tool_name="calendar.update_event",
            run_id="run-1",
            user_id="user-1",
            arguments={"event_id": "evt-123", "calendar_id": "primary"},
        )
        # Calling without expected_target_fingerprint fails closed in every environment (H3)
        with pytest.raises(PermissionDeniedError, match="missing required target fingerprint"):
            await require_mutation_approval(
                tool_name="calendar.update_event",
                context_token=token,
                arguments={"event_id": "evt-123", "calendar_id": "primary"},
                run_id="run-1",
                user_id="user-1",
                consume=False,
                expected_target_fingerprint=None,
            )

    @pytest.mark.asyncio
    async def test_creation_tools_allow_none_fingerprint(self) -> None:
        token = generate_approval_token(
            approval_id="appr-create",
            tool_name="calendar.create_event",
            run_id="run-1",
            user_id="user-1",
            arguments={"summary": "New Event"},
        )
        # Creation tools are not in STALE_CHECK_FINGERPRINT_TOOLS and pass without fingerprint
        assert "calendar.create_event" not in STALE_CHECK_FINGERPRINT_TOOLS
        await require_mutation_approval(
            tool_name="calendar.create_event",
            context_token=token,
            arguments={"summary": "New Event"},
            run_id="run-1",
            user_id="user-1",
            consume=False,
        )


class TestH4ExecutionTokenHashing:
    """H4: Execution token must be hashed in token_hash and not saved plaintext."""

    def test_token_hash_model_column_exists(self) -> None:
        row = ApprovalRequestRow(
            run_id="run-1",
            action_type="calendar.update_event",
            description="desc",
            risk_level="high_impact_write",
            status="approved",
            token_hash="hash123",
        )
        assert row.token_hash == "hash123"
        assert not hasattr(row, "execution_token") or getattr(row, "execution_token", None) is None


class TestH5ConsumedStoreFailClosed:
    """H5: Consumed-store must raise RuntimeError in staging/production on Redis failure."""

    @pytest.mark.asyncio
    async def test_async_try_consume_raises_in_production(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.set.side_effect = ConnectionError("Redis server unreachable")
        store = RedisConsumedTokenStore(mock_redis)

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.environment = Environment.PRODUCTION
            with pytest.raises(RuntimeError, match="Redis consumed token store is unavailable"):
                await store.try_consume("nonce_key", ttl_seconds=60)

    def test_sync_try_consume_raises_in_production(self) -> None:
        mock_redis = AsyncMock()
        store = RedisConsumedTokenStore(mock_redis)

        with patch.object(store, "_get_sync_client") as mock_client_getter:
            mock_sync_client = MagicMock()
            mock_sync_client.set.side_effect = ConnectionError("Redis down")
            mock_client_getter.return_value = mock_sync_client

            with patch("app.core.config.settings") as mock_settings:
                mock_settings.environment = Environment.PRODUCTION
                with pytest.raises(RuntimeError, match="Redis consumed token store is unavailable"):
                    store.try_consume_sync("nonce_key", ttl_seconds=60)


class TestH6ProductionCredentialsValidation:
    """H6: Default credentials must be rejected in production/staging."""

    def test_reject_default_postgres_in_production(self) -> None:
        with pytest.raises(ValueError, match="Default database credentials/host"):
            Settings(
                environment=Environment.PRODUCTION,
                database={"url": "postgresql+asyncpg://postgres:postgres@localhost:5434/assistant"},
                security={"approval_signing_key": "x" * 32},
                _env_file=None,  # type: ignore[call-arg]
            )

    def test_reject_redis_without_password_in_production(self) -> None:
        with pytest.raises(ValueError, match="Redis URL must include a password"):
            Settings(
                environment=Environment.PRODUCTION,
                database={"url": "postgresql+asyncpg://prod:secret@db.example.com:5432/assistant"},
                redis={"url": "redis://localhost:6379/0"},
                security={"approval_signing_key": "x" * 32},
                _env_file=None,  # type: ignore[call-arg]
            )


class TestH7IDOROwnershipValidation:
    """H7: IDOR must fail-closed when run is missing or does not match caller."""

    @pytest.mark.asyncio
    async def test_question_plane_rejects_missing_or_mismatched_run_owner(self) -> None:
        mock_session = AsyncMock()
        mock_question = MagicMock()
        mock_question.id = "q-1"
        mock_question.run_id = "run-missing"
        mock_question.status = "pending"
        mock_question.expires_at = None
        mock_session.scalar.return_value = mock_question

        with patch(
            "app.services.platform.run_persistence.RunPersistenceService.get_run", return_value=None
        ):
            with pytest.raises(PermissionError, match="does not belong to expected user"):
                await QuestionPlaneService.record_answers(
                    session=mock_session,
                    question_id="q-1",
                    answers=[UserQuestionAnswer(question_id="item-1", selected_options=["optA"])],
                    answered_by="attacker",
                    expected_user_id="victim",
                )

    @pytest.mark.asyncio
    async def test_orphan_question_without_run_is_forbidden(self) -> None:
        mock_session = AsyncMock()
        mock_question = MagicMock()
        mock_question.id = "q-orphan"
        mock_question.run_id = None
        mock_question.status = "pending"
        mock_question.expires_at = None
        mock_session.scalar.return_value = mock_question

        with pytest.raises(PermissionError, match="does not belong to expected user"):
            await QuestionPlaneService.record_answers(
                session=mock_session,
                question_id="q-orphan",
                answers=[UserQuestionAnswer(question_id="item-1", selected_options=["optA"])],
                answered_by="attacker",
                expected_user_id="victim",
            )


class TestM1ProposalHashBinding:
    """M1: Proposal hash must bind target and important_arguments."""

    def test_different_targets_produce_different_hashes(self) -> None:
        a1 = ProposedAction(
            action_type="calendar.update_event",
            description="Update event",
            target="event_alpha",
            parameters={"calendar_id": "primary"},
            risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        )
        a2 = ProposedAction(
            action_type="calendar.update_event",
            description="Update event",
            target="event_beta",
            parameters={"calendar_id": "primary"},
            risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        )
        from app.services.approvals import _proposal_hash

        assert _proposal_hash(a1) != _proposal_hash(a2)


class TestM2DelegationNeverCheck:
    """M2: Delegation NEVER must be rejected in require_mutation_approval."""

    @pytest.mark.asyncio
    async def test_delegation_never_fails(self) -> None:
        with pytest.raises(PermissionDeniedError, match="delegation is set to NEVER"):
            await require_mutation_approval(
                tool_name="gmail.send_draft",
                context_token="appr_fake.sig",
                arguments={},
                delegation="NEVER",
            )

    @pytest.mark.asyncio
    async def test_delegation_context_never_fails(self) -> None:
        from app.domain.models import DelegationContext

        pinned = DelegationContext(
            parent_agent="CommunicationAgent",
            target_agent="CalendarAgent",
            approval_policy="NEVER",
        )
        with pytest.raises(PermissionDeniedError, match="delegation is set to NEVER"):
            await require_mutation_approval(
                tool_name="gmail.send_draft",
                context_token="appr_fake.sig",
                arguments={},
                delegation=pinned,
            )


class TestM3AskUserInputValidation:
    """M3: ask_user must reject unbounded or spammy question payloads."""

    @pytest.mark.asyncio
    async def test_ask_user_rejects_more_than_5_questions(self) -> None:
        questions = [{"id": f"q{i}", "question": f"Question {i}?"} for i in range(6)]
        ctx = ToolContext(user_id="user1", run_id="run1")
        res = await execute_ask_user(
            ToolInput(tool_name="ask_user", arguments={"questions": questions}),
            ctx,
        )
        assert not res.success
        assert "Too many question items" in (res.error or "")

    @pytest.mark.asyncio
    async def test_ask_user_rejects_excessive_question_length(self) -> None:
        questions = [{"id": "q1", "question": "A" * 1500}]
        ctx = ToolContext(user_id="user1", run_id="run1")
        res = await execute_ask_user(
            ToolInput(tool_name="ask_user", arguments={"questions": questions}),
            ctx,
        )
        assert not res.success
        assert "<= 1000 chars" in (res.error or "")


class TestM6FlatLLMKeyAliases:
    """M6: Flat OPENAI_API_KEY must populate nested llm settings."""

    def test_flat_keys_merge_into_llm_settings(self) -> None:
        cfg = Settings(
            environment=Environment.DEVELOPMENT,
            openai_api_key="sk-openai-test",
            anthropic_api_key="sk-ant-test",
            _env_file=None,  # type: ignore[call-arg]
        )
        assert cfg.llm.openai_api_key == "sk-openai-test"
        assert cfg.llm.anthropic_api_key == "sk-ant-test"

    def test_whitespace_only_flat_keys_do_not_clobber_llm(self) -> None:
        cfg = Settings(
            environment=Environment.DEVELOPMENT,
            openai_api_key="   ",
            _env_file=None,  # type: ignore[call-arg]
        )
        assert not (cfg.llm.openai_api_key or "").strip()


class TestM11CalendarAgentCapabilities:
    """M11: CALENDAR_AGENT must include contacts.resolve_person per §8.2."""

    def test_calendar_agent_has_contacts_capability(self) -> None:
        assert "contacts.resolve_person" in CALENDAR_AGENT.capabilities
        assert "contacts" in CALENDAR_AGENT.allowed_tool_categories
