"""Direct tests for HMAC approval tokens (H1/H2/H6/M1/M7)."""

from __future__ import annotations

import uuid

import pytest

from app.domain.errors import PermissionDeniedError
from app.services.approvals import (
    canonical_proposal_hash,
    generate_approval_token,
    require_mutation_approval,
    resolve_approval_token,
    verify_approval_token,
    verify_approval_token_sync,
)
from app.services.consumed_store import (
    InMemoryConsumedTokenStore,
    RedisConsumedTokenStore,
)


@pytest.mark.asyncio
async def test_peek_does_not_consume_single_use_token() -> None:
    store = InMemoryConsumedTokenStore()
    token = generate_approval_token("appr-1", tool_name="calendar.create_event", run_id="run-1")

    assert await verify_approval_token(
        token, tool_name=None, consume=False, store=store, expected_run_id="run-1"
    )
    assert await verify_approval_token(
        token, tool_name="calendar.create_event", consume=False, store=store
    )
    assert await verify_approval_token(
        token,
        tool_name="calendar.create_event",
        consume=True,
        store=store,
        expected_run_id="run-1",
    )
    assert not await verify_approval_token(
        token,
        tool_name="calendar.create_event",
        consume=True,
        store=store,
        expected_run_id="run-1",
    )
    assert not await verify_approval_token(
        token, tool_name="calendar.create_event", consume=False, store=store
    )


@pytest.mark.asyncio
async def test_tool_scope_mismatch_rejected_on_execute_but_peek_skips_tool() -> None:
    store = InMemoryConsumedTokenStore()
    token = generate_approval_token("appr-2", tool_name="gmail.create_draft")

    assert await verify_approval_token(token, tool_name=None, consume=False, store=store)
    assert not await verify_approval_token(
        token, tool_name="gmail.send_draft", consume=False, store=store
    )
    assert await verify_approval_token(
        token, tool_name="gmail.create_draft", consume=True, store=store
    )


@pytest.mark.asyncio
async def test_consume_requires_matching_run_id_when_bound() -> None:
    store = InMemoryConsumedTokenStore()
    token = generate_approval_token("appr-3", tool_name="drive.upload_file", run_id="run-abc")

    assert not await verify_approval_token(
        token,
        tool_name="drive.upload_file",
        consume=True,
        store=store,
        expected_run_id="other-run",
    )
    assert await verify_approval_token(
        token,
        tool_name="drive.upload_file",
        consume=True,
        store=store,
        expected_run_id="run-abc",
    )


def test_verify_sync_peek_then_consume() -> None:
    store = InMemoryConsumedTokenStore()
    token = generate_approval_token("appr-4", tool_name="gmail.create_draft")
    assert verify_approval_token_sync(token, tool_name=None, consume=False, store=store)
    assert verify_approval_token_sync(
        token, tool_name="gmail.create_draft", consume=True, store=store
    )
    assert not verify_approval_token_sync(
        token, tool_name="gmail.create_draft", consume=True, store=store
    )


@pytest.mark.asyncio
async def test_resolve_and_require_reject_uuid_approval_id() -> None:
    approval_id = str(uuid.uuid4())
    assert resolve_approval_token(None, {"approval_id": approval_id}) is None

    with pytest.raises(PermissionDeniedError, match="approval_id is a request UUID"):
        await require_mutation_approval(
            tool_name="calendar.create_event",
            context_token=None,
            arguments={"approval_id": approval_id},
        )


@pytest.mark.asyncio
async def test_require_rejects_argument_substitution() -> None:
    store = InMemoryConsumedTokenStore()
    approved = {"to": "alice@example.com", "amount": 10}
    token = generate_approval_token(
        "appr-sub",
        tool_name="gmail.send_draft",
        run_id="run-1",
        arguments=approved,
    )
    with pytest.raises(PermissionDeniedError):
        await require_mutation_approval(
            tool_name="gmail.send_draft",
            context_token=token,
            arguments={"to": "eve@evil.com", "amount": 10},
            run_id="run-1",
            store=store,
        )
    await require_mutation_approval(
        tool_name="gmail.send_draft",
        context_token=token,
        arguments=approved,
        run_id="run-1",
        store=store,
    )


@pytest.mark.asyncio
async def test_peek_with_expected_run_id_rejects_unbound_token() -> None:
    store = InMemoryConsumedTokenStore()
    token = generate_approval_token("appr-unbound", tool_name="gmail.create_draft")
    assert not await verify_approval_token(
        token, tool_name=None, consume=False, store=store, expected_run_id="run-1"
    )


@pytest.mark.asyncio
async def test_consume_proposal_bound_without_expected_hash_rejected_in_strict_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Environment, settings

    monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
    monkeypatch.setattr(settings.security, "approval_signing_key", "a" * 32)
    store = InMemoryConsumedTokenStore()
    args = {"to": "bob@example.com", "subject": "Hello"}
    token = generate_approval_token(
        "appr-strict",
        tool_name="gmail.send_draft",
        run_id="run-strict-1",
        user_id="user-strict-1",
        arguments=args,
    )
    # Calling without expected_proposal_hash must be rejected when consume=True in strict env
    assert not await verify_approval_token(
        token,
        tool_name="gmail.send_draft",
        consume=True,
        store=store,
        expected_run_id="run-strict-1",
        expected_user_id="user-strict-1",
    )
    # With matching expected_proposal_hash, it succeeds
    expected_hash = canonical_proposal_hash("gmail.send_draft", args)
    assert await verify_approval_token(
        token,
        tool_name="gmail.send_draft",
        consume=True,
        store=store,
        expected_run_id="run-strict-1",
        expected_user_id="user-strict-1",
        expected_proposal_hash=expected_hash,
    )


def test_consumed_token_store_sync_methods() -> None:
    store = InMemoryConsumedTokenStore()
    key = "sync_nonce_1"
    assert not store.is_consumed_sync(key)
    assert store.try_consume_sync(key, ttl_seconds=60) is True
    assert store.is_consumed_sync(key) is True
    # Second consume must fail (single-use)
    assert store.try_consume_sync(key, ttl_seconds=60) is False


def test_redis_consumed_token_store_sync_fallback() -> None:
    class MockSyncRedis:
        def __init__(self) -> None:
            self.data: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.data.get(key)

        def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> bool:
            if nx and key in self.data:
                return False
            self.data[key] = value
            return True

    mock_sync = MockSyncRedis()
    store = RedisConsumedTokenStore(redis_client=None, sync_redis_client=mock_sync)
    key = "test_key_sync"
    assert not store.is_consumed_sync(key)
    assert store.try_consume_sync(key, ttl_seconds=30) is True
    assert store.is_consumed_sync(key) is True
    assert store.try_consume_sync(key, ttl_seconds=30) is False


@pytest.mark.asyncio
async def test_stale_check_skipped_when_expected_fingerprint_absent() -> None:
    """Omitting expected_* is allowed on create tools; update/delete always fail closed (H3)."""
    store = InMemoryConsumedTokenStore()
    args_create = {"summary": "New Event"}
    token_create = generate_approval_token(
        "appr-create",
        tool_name="calendar.create_event",
        run_id="run-fp",
        arguments=args_create,
    )
    await require_mutation_approval(
        tool_name="calendar.create_event",
        context_token=token_create,
        arguments=args_create,
        run_id="run-fp",
        store=store,
    )

    args_delete = {"event_id": "ev_1"}
    token_delete = generate_approval_token(
        "appr-no-fp",
        tool_name="calendar.delete_event",
        run_id="run-fp",
        arguments=args_delete,
    )
    with pytest.raises(PermissionDeniedError, match="missing required target fingerprint"):
        await require_mutation_approval(
            tool_name="calendar.delete_event",
            context_token=token_delete,
            arguments=args_delete,
            run_id="run-fp",
            user_id="user-1",
            store=store,
            current_target_fingerprint="etag_now",
        )


@pytest.mark.asyncio
async def test_stale_check_rejects_expected_version_mismatch() -> None:
    store = InMemoryConsumedTokenStore()
    args = {"file_id": "f1", "expected_version": "v1"}
    token = generate_approval_token(
        "appr-ver",
        tool_name="drive.delete_file",
        run_id="run-ver",
        arguments=args,
    )
    with pytest.raises(PermissionDeniedError, match="stale target state"):
        await require_mutation_approval(
            tool_name="drive.delete_file",
            context_token=token,
            arguments=args,
            run_id="run-ver",
            store=store,
            current_target_fingerprint="v2",
        )


def test_current_fingerprint_excluded_from_proposal_hash() -> None:
    base = {"file_id": "f1", "expected_version": "v1"}
    with_live = {**base, "current_version": "v2"}
    assert canonical_proposal_hash("drive.delete_file", base) == canonical_proposal_hash(
        "drive.delete_file", with_live
    )


def test_canonical_hash_does_not_truncate_before_hash() -> None:
    """Distinct suffixes must not collide; sanitization is display-only (H2)."""
    prefix = "x" * 1000
    raw_a = {"body": prefix + "ALPHA", "to": "a@example.com"}
    raw_b = {"body": prefix + "BRAVO", "to": "a@example.com"}
    assert canonical_proposal_hash("gmail.send", raw_a) != canonical_proposal_hash(
        "gmail.send", raw_b
    )


def test_canonical_hash_rejects_over_quota() -> None:
    from app.services.approval_tokens import MAX_PROPOSAL_HASH_BYTES

    huge = {"body": "x" * (MAX_PROPOSAL_HASH_BYTES + 8)}
    with pytest.raises(ValueError, match="hash quota"):
        canonical_proposal_hash("gmail.send", huge)


def test_decode_rejects_non_dict_and_non_int_exp() -> None:
    import base64
    import hashlib
    import hmac
    import json

    from app.services.approvals import (
        APPROVAL_TOKEN_PREFIX,
        _decode_approval_payload,
        _get_signing_key,
    )

    def _sign(raw: bytes) -> str:
        sig = hmac.new(_get_signing_key(), raw, hashlib.sha256).hexdigest()
        b64 = base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")
        return f"{APPROVAL_TOKEN_PREFIX}{b64}.{sig}"

    assert _decode_approval_payload(_sign(b"[1, 2]")) is None
    assert (
        _decode_approval_payload(
            _sign(json.dumps({"id": "a", "tool": "t", "exp": "soon"}).encode())
        )
        is None
    )


@pytest.mark.asyncio
async def test_peek_in_production_requires_expected_run_id_and_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Environment, settings

    monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
    monkeypatch.setattr(settings.security, "approval_signing_key", "a" * 32)
    store = InMemoryConsumedTokenStore()
    args: dict[str, object] = {}
    token = generate_approval_token(
        "appr-peek-prod",
        tool_name="gmail.send_draft",
        run_id="run-peek",
        user_id="user-peek",
        arguments=args,
    )
    expected_hash = canonical_proposal_hash("gmail.send_draft", args)
    assert not await verify_approval_token(
        token, tool_name="gmail.send_draft", consume=False, store=store
    )
    assert not await verify_approval_token(
        token,
        tool_name="gmail.send_draft",
        consume=False,
        store=store,
        expected_run_id="run-peek",
        expected_user_id="user-peek",
    )
    assert not await verify_approval_token(
        token,
        tool_name=None,
        consume=False,
        store=store,
        expected_run_id="run-peek",
        expected_user_id="user-peek",
        expected_proposal_hash=expected_hash,
    )
    assert await verify_approval_token(
        token,
        tool_name="gmail.send_draft",
        consume=False,
        store=store,
        expected_run_id="run-peek",
        expected_user_id="user-peek",
        expected_proposal_hash=expected_hash,
    )


@pytest.mark.asyncio
async def test_peek_requires_proposal_hash_when_token_binds_one() -> None:
    """Hashed tokens must not peek-pass when the caller omits expected_proposal_hash (N3)."""
    store = InMemoryConsumedTokenStore()
    args: dict[str, object] = {"to": "a@b.com"}
    token = generate_approval_token(
        "appr-n3",
        tool_name="gmail.create_draft",
        run_id="run-n3",
        arguments=args,
    )
    expected = canonical_proposal_hash("gmail.create_draft", args)
    assert not await verify_approval_token(
        token,
        tool_name="gmail.create_draft",
        consume=False,
        store=store,
        expected_run_id="run-n3",
    )
    assert not await verify_approval_token(
        token,
        tool_name="gmail.create_draft",
        consume=False,
        store=store,
        expected_run_id="run-n3",
        expected_proposal_hash=canonical_proposal_hash("gmail.create_draft", {"to": "other@b.com"}),
    )
    assert await verify_approval_token(
        token,
        tool_name="gmail.create_draft",
        consume=False,
        store=store,
        expected_run_id="run-n3",
        expected_proposal_hash=expected,
    )
