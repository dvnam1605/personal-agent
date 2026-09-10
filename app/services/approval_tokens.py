"""HMAC approval tokens, stale-target fingerprints, and the mutation gate.

Split out of ``approvals.py`` so request lifecycle and crypto do not share one
god-file (L3). Callers may keep importing from ``app.services.approvals``.
"""

from __future__ import annotations

import atexit
import base64
import binascii
import hashlib
import hmac
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from orjson import JSONDecodeError as OrjsonDecodeError

from app.core.jsonutil import dumps_canonical
from app.core.jsonutil import loads as json_loads
from app.services.consumed_store import ConsumedTokenStore, get_default_store

_token_logger = logging.getLogger(__name__)

# Deterministic test-only signing key; NEVER used outside DEVELOPMENT/TESTING.
_TEST_SIGNING_KEY = b"test-only-approval-signing-key-32B"
_SYNC_VERIFY_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="approval-verify")
atexit.register(_SYNC_VERIFY_POOL.shutdown, wait=False)
_SYNC_VERIFY_TIMEOUT_SECONDS = 10.0
APPROVAL_TOKEN_PREFIX = "appr_"
# Hash the raw canonical payload. Truncating before sha256 collides distinct bodies (H2).
MAX_PROPOSAL_HASH_BYTES = 65_536

_TOKEN_HASH_EXCLUDED = frozenset(
    {
        "approval_token",
        "approval_id",
        "current_target_fingerprint",
        "current_etag",
        "current_version",
    }
)

_EXPECTED_FINGERPRINT_KEYS = (
    "expected_target_fingerprint",
    "expected_etag",
    "expected_version",
)
_CURRENT_FINGERPRINT_KEYS = (
    "current_target_fingerprint",
    "current_etag",
    "current_version",
)

# Tools whose Google resource carries an ETag / version token. Callers MUST pass
# expected_etag (Calendar) or expected_version (Drive) from the last read.
# Create-style tools (calendar.create_event, gmail.create_draft, drive.upload_file)
# are not in this set and may omit fingerprints.
STALE_CHECK_ETAG_TOOLS = frozenset(
    {
        "calendar.update_event",
        "calendar.delete_event",
        "calendar.add_attendee",
        "calendar.remove_attendee",
    }
)
STALE_CHECK_VERSION_TOOLS = frozenset(
    {
        "drive.move_file",
        "drive.rename_file",
        "drive.delete_file",
        "drive.update_permissions",
    }
)
STALE_CHECK_FINGERPRINT_TOOLS = STALE_CHECK_ETAG_TOOLS | STALE_CHECK_VERSION_TOOLS


def is_signed_approval_token(value: str | None) -> bool:
    """Return True if *value* looks like a signed ``appr_`` execution token."""
    return bool(value) and value.startswith(APPROVAL_TOKEN_PREFIX) and "." in value


def resolve_approval_token(
    context_token: str | None,
    arguments: dict[str, Any] | None = None,
) -> str | None:
    """Return the HMAC execution token. A UUID ``approval_id`` is never a token."""
    args = arguments or {}
    for candidate in (context_token, args.get("approval_token")):
        if isinstance(candidate, str) and is_signed_approval_token(candidate):
            return candidate
    approval_id = args.get("approval_id")
    if isinstance(approval_id, str) and is_signed_approval_token(approval_id):
        return approval_id
    return None


def approval_id_without_token(arguments: dict[str, Any] | None = None) -> str | None:
    """Return a non-token ``approval_id`` UUID if present (needs a DB lookup)."""
    approval_id = (arguments or {}).get("approval_id")
    if isinstance(approval_id, str) and approval_id and not is_signed_approval_token(approval_id):
        return approval_id
    return None


def _first_nonblank_str(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def expected_target_fingerprint_from_arguments(arguments: dict[str, Any] | None) -> str | None:
    """Return the caller-supplied expected fingerprint, if any."""
    return _first_nonblank_str(arguments or {}, _EXPECTED_FINGERPRINT_KEYS)


def current_target_fingerprint_from_arguments(arguments: dict[str, Any] | None) -> str | None:
    """Return a caller-supplied live fingerprint (not bound into the token hash)."""
    return _first_nonblank_str(arguments or {}, _CURRENT_FINGERPRINT_KEYS)


def _is_relaxed_token_env() -> bool:
    from app.core.config import Environment, settings

    return settings.environment in (Environment.DEVELOPMENT, Environment.TESTING)


def _sha256_canonical(payload: object) -> str:
    """SHA-256 of sorted-key JSON. Reject oversized payloads instead of truncating."""
    raw = dumps_canonical(payload)
    if len(raw) > MAX_PROPOSAL_HASH_BYTES:
        raise ValueError(
            f"Proposal payload exceeds hash quota ({len(raw)} > {MAX_PROPOSAL_HASH_BYTES} bytes)."
        )
    return hashlib.sha256(raw).hexdigest()


def canonical_proposal_hash(tool_name: str, arguments: dict[str, Any] | None) -> str:
    """Hash ``tool_name`` plus raw canonical JSON arguments (token fields stripped).

    Display sanitization must never run before this hash: truncated strings collide.
    """
    clean = {
        key: value
        for key, value in dict(arguments or {}).items()
        if key not in _TOKEN_HASH_EXCLUDED
    }
    return _sha256_canonical({"tool": tool_name, "arguments": clean})


def approval_token_bound_tool(token: str) -> str | None:
    """Return the tool name bound into a signed token, or None if wildcard/invalid."""
    payload = _decode_approval_payload(token)
    if payload is None:
        return None
    tool = payload.get("tool")
    if isinstance(tool, str) and tool.strip() and tool != "*":
        return tool.strip()
    return None


def execution_token_for_request(request: Any, user_id: str | None = None) -> str:
    """Mint a run-, user-, and argument-bound execution token for an approved request."""
    if not request.tool_name:
        raise ValueError("Cannot mint an execution token without tool_name.")
    if request.token_hash:
        raise ValueError("Execution token already issued for this approval request.")
    params = request.parameters if isinstance(request.parameters, dict) else {}
    resolved_user = user_id or request.approver_id
    return generate_approval_token(
        request.id,
        tool_name=request.tool_name,
        run_id=request.run_id,
        user_id=resolved_user,
        arguments=params,
        nonce=uuid.uuid4().hex,
    )


def _get_signing_key(secret_key: str | None = None) -> bytes:
    """Return the HMAC key for approval tokens."""
    if secret_key:
        raw = secret_key.encode("utf-8")
        if len(raw) < 32:
            raise ValueError("Approval signing key must be at least 32 bytes.")
        return raw

    from app.core.config import Environment, settings

    configured = settings.security.approval_signing_key
    if configured:
        raw = configured.encode("utf-8")
        if len(raw) < 32:
            raise ValueError("APPROVAL_SIGNING_KEY is too short (minimum 32 bytes).")
        return raw

    if settings.environment not in (Environment.DEVELOPMENT, Environment.TESTING):
        raise ValueError(
            "APPROVAL_SIGNING_KEY must be configured in staging/production. "
            "Set SECURITY__APPROVAL_SIGNING_KEY in your environment."
        )
    return _TEST_SIGNING_KEY


def generate_approval_token(
    approval_id: str,
    tool_name: str | None = None,
    expires_in_seconds: int = 900,
    single_use: bool = True,
    secret_key: str | None = None,
    *,
    run_id: str | None = None,
    user_id: str | None = None,
    proposal_hash: str | None = None,
    arguments: dict[str, Any] | None = None,
    nonce: str | None = None,
    created_at: int | None = None,
) -> str:
    """Generate a signed, tamper-evident, scoped execution token for an approved action."""
    resolved_tool = tool_name or "*"
    if not _is_relaxed_token_env():
        if resolved_tool == "*":
            raise ValueError("Wildcard tool='*' approval tokens are banned outside dev/test.")
        if not single_use:
            raise ValueError(
                "Multi-use approval tokens (single_use=False) are banned outside dev/test."
            )
        if not run_id:
            raise ValueError("run_id is required on approval tokens outside dev/test.")
        if not user_id:
            raise ValueError("user_id is required on approval tokens outside dev/test.")

    bound_hash = proposal_hash
    if bound_hash is None and arguments is not None:
        bound_hash = canonical_proposal_hash(resolved_tool, arguments)

    now = created_at if created_at is not None else int(time.time())
    payload: dict[str, Any] = {
        "id": approval_id,
        "tool": resolved_tool,
        "exp": now + expires_in_seconds,
        "single_use": single_use,
        "nonce": nonce or uuid.uuid4().hex[:16],
    }
    if run_id is not None:
        payload["run_id"] = run_id
    if user_id is not None:
        payload["user_id"] = user_id
    if bound_hash is not None:
        payload["proposal_hash"] = bound_hash

    payload_bytes = dumps_canonical(payload)
    sig = hmac.new(_get_signing_key(secret_key), payload_bytes, hashlib.sha256).hexdigest()
    b64_payload = base64.urlsafe_b64encode(payload_bytes).decode("utf-8").rstrip("=")
    return f"{APPROVAL_TOKEN_PREFIX}{b64_payload}.{sig}"


def _decode_approval_payload(
    token: str,
    secret_key: str | None = None,
) -> dict[str, Any] | None:
    """Decode and authenticate a token. Returns None if malformed/forged/expired."""
    if not is_signed_approval_token(token):
        return None
    parts = token[len(APPROVAL_TOKEN_PREFIX) :].split(".")
    if len(parts) != 2:
        return None
    b64_payload, signature = parts
    padding = "=" * (-len(b64_payload) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(b64_payload + padding)
        expected_sig = hmac.new(
            _get_signing_key(secret_key), payload_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            return None
        payload = json_loads(payload_bytes)
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError, OrjsonDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    if exp <= int(time.time()):
        return None
    return payload


def _consume_key(payload: dict[str, Any]) -> str:
    nonce = str(payload.get("nonce") or "")
    digest = str(payload.get("proposal_hash") or "")[:16]
    return f"{payload.get('id')}:{nonce}:{digest}"


def _remaining_ttl_seconds(payload: dict[str, Any]) -> int:
    remaining = int(payload.get("exp", 0)) - int(time.time())
    return max(remaining, 1)


def _token_policy_allows(
    payload: dict[str, Any],
    *,
    tool_name: str | None,
    consume: bool,
    expected_run_id: str | None,
    expected_user_id: str | None = None,
    expected_proposal_hash: str | None,
) -> bool:
    """Verifier-side policy: reject wildcards/multi-use in prod; bind run/user/hash."""
    scoped_tool = payload.get("tool", "*")
    if tool_name is not None:
        if scoped_tool != "*" and scoped_tool != tool_name:
            return False
    if scoped_tool == "*" and not _is_relaxed_token_env():
        return False
    if not payload.get("single_use", True) and not _is_relaxed_token_env():
        return False

    bound_run = payload.get("run_id")
    bound_user = payload.get("user_id")
    bound_hash = payload.get("proposal_hash")
    strict = not _is_relaxed_token_env()
    # Peek in production must prove run, user, and that the token names a tool.
    # Omitting tool_name previously let a calendar.create token advertise gmail.send (M4).
    bind_run = consume or strict
    bind_user = consume or strict
    if tool_name is None and strict:
        return False

    if expected_run_id is not None:
        if bound_run != expected_run_id:
            return False
    elif bind_run and bound_run is None and strict:
        return False
    elif bind_run and bound_run is not None and expected_run_id is None and strict:
        return False

    if expected_user_id is not None:
        if bound_user is not None and bound_user != expected_user_id:
            return False
        if bound_user is None and strict:
            return False
    elif bind_user and bound_user is None and strict:
        return False
    elif bind_user and bound_user is not None and expected_user_id is None and strict:
        return False

    if expected_proposal_hash is not None:
        if bound_hash != expected_proposal_hash:
            return False
    elif bound_hash is not None:
        if not consume:
            # Peek used to pass in pytest when the caller omitted the hash (N3).
            return False
        if strict:
            return False
    elif consume and strict:
        return False
    elif not consume and strict:
        return False
    return True


async def verify_approval_token(
    token: str,
    tool_name: str | None = None,
    consume: bool = True,
    secret_key: str | None = None,
    *,
    store: ConsumedTokenStore | None = None,
    expected_run_id: str | None = None,
    expected_user_id: str | None = None,
    expected_proposal_hash: str | None = None,
) -> bool:
    """Validate signature, expiry, optional tool scope, binding, and single-use."""
    payload = _decode_approval_payload(token, secret_key=secret_key)
    if payload is None:
        return False
    if not _token_policy_allows(
        payload,
        tool_name=tool_name,
        consume=consume,
        expected_run_id=expected_run_id,
        expected_user_id=expected_user_id,
        expected_proposal_hash=expected_proposal_hash,
    ):
        return False

    if not payload.get("single_use", True):
        return True

    token_key = _consume_key(payload)
    if store is None:
        store = get_default_store()

    if consume:
        ttl = _remaining_ttl_seconds(payload)
        consumed = await store.try_consume(token_key, ttl)
        return bool(consumed)

    already = await store.is_consumed(token_key)
    return not already


def verify_approval_token_sync(
    token: str,
    tool_name: str | None = None,
    consume: bool = True,
    secret_key: str | None = None,
    *,
    store: ConsumedTokenStore | None = None,
    expected_run_id: str | None = None,
    expected_user_id: str | None = None,
    expected_proposal_hash: str | None = None,
) -> bool:
    """Synchronous verification wrapper for LangGraph sync nodes and tests."""
    payload = _decode_approval_payload(token, secret_key=secret_key)
    if payload is None:
        return False
    if not _token_policy_allows(
        payload,
        tool_name=tool_name,
        consume=consume,
        expected_run_id=expected_run_id,
        expected_user_id=expected_user_id,
        expected_proposal_hash=expected_proposal_hash,
    ):
        return False
    if not payload.get("single_use", True):
        return True

    resolved = store if store is not None else get_default_store()
    token_key = _consume_key(payload)
    try_consume_sync = getattr(resolved, "try_consume_sync", None)
    is_consumed_sync = getattr(resolved, "is_consumed_sync", None)
    if callable(try_consume_sync) and callable(is_consumed_sync):
        if consume:
            return bool(try_consume_sync(token_key, _remaining_ttl_seconds(payload)))
        return not bool(is_consumed_sync(token_key))

    import asyncio

    coro = verify_approval_token(
        token,
        tool_name,
        consume=consume,
        secret_key=secret_key,
        store=resolved,
        expected_run_id=expected_run_id,
        expected_user_id=expected_user_id,
        expected_proposal_hash=expected_proposal_hash,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        try:
            return _SYNC_VERIFY_POOL.submit(asyncio.run, coro).result(
                timeout=_SYNC_VERIFY_TIMEOUT_SECONDS
            )
        except (FuturesTimeoutError, TimeoutError):
            _token_logger.error("approval_token_sync_verify_timeout")
            return False
    return asyncio.run(coro)


def _delegation_pins_never(delegation: object | None) -> bool:
    """True when a specialist delegation (or string pin) forbids self-approval."""
    if delegation is None:
        return False
    if isinstance(delegation, str):
        return delegation.strip().upper() == "NEVER"
    policy = getattr(delegation, "approval_policy", None)
    if policy is None:
        return False
    value = getattr(policy, "value", policy)
    return str(value).strip().upper() == "NEVER"


async def require_mutation_approval(
    *,
    tool_name: str,
    context_token: str | None,
    arguments: dict[str, Any],
    run_id: str | None = None,
    user_id: str | None = None,
    delegation: object | None = None,
    consume: bool = True,
    store: ConsumedTokenStore | None = None,
    current_target_fingerprint: str | None = None,
    expected_target_fingerprint: str | None = None,
) -> None:
    """Fail-closed mutation gate shared by Calendar / Gmail / Drive tools.

    Stale-target check (H3): tools in ``STALE_CHECK_FINGERPRINT_TOOLS`` always
    require expected and current fingerprints, including in development/testing.
    Create-style tools without an existing target may omit fingerprints.
    """
    from app.domain.enums import ActionRiskLevel
    from app.domain.errors import PermissionDeniedError
    from app.domain.models import ProposedAction
    from app.services.policy_engine import PolicyEngine

    if _delegation_pins_never(delegation):
        raise PermissionDeniedError(
            f"Mutation tool '{tool_name}' rejected: specialist delegation is set to NEVER."
        )

    if approval_id_without_token(arguments):
        raise PermissionDeniedError(
            f"Mutation tool '{tool_name}' rejected: approval_id is a request UUID, "
            "not an execution token. Pass approval_token (appr_...). "
            "Lookup of approval_id belongs on the approval service, not the tool."
        )

    token = resolve_approval_token(context_token, arguments)
    if not token:
        supplied = context_token or (arguments or {}).get("approval_token")
        if isinstance(supplied, str) and supplied.strip():
            raise PermissionDeniedError(
                f"Mutation tool '{tool_name}' rejected: approval token is invalid, expired, or unverified."
            )
        raise PermissionDeniedError(
            f"Mutation tool '{tool_name}' requires human approval verification "
            "(missing signed approval_token)."
        )

    curr_fp = current_target_fingerprint or current_target_fingerprint_from_arguments(arguments)
    exp_fp = expected_target_fingerprint or expected_target_fingerprint_from_arguments(arguments)
    if tool_name in STALE_CHECK_FINGERPRINT_TOOLS:
        if exp_fp is None:
            raise PermissionDeniedError(
                f"Mutation tool '{tool_name}' rejected: missing required target fingerprint "
                "(expected_etag/expected_version) for stale-check."
            )
        if curr_fp is None:
            raise PermissionDeniedError(
                f"Mutation tool '{tool_name}' rejected: current resource fingerprint "
                "could not be determined."
            )
    if exp_fp is not None:
        action = ProposedAction(
            action_type=tool_name,
            description=f"Mutation call to {tool_name}",
            tool_name=tool_name,
            parameters=arguments or {},
            risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
            requires_approval=True,
        )
        if not PolicyEngine.validate_target_state(action, curr_fp, exp_fp):
            raise PermissionDeniedError(
                f"Mutation tool '{tool_name}' rejected: target resource has changed "
                "since approval (stale target state)."
            )
    ok = await verify_approval_token(
        token,
        tool_name,
        consume=consume,
        store=store,
        expected_run_id=run_id,
        expected_user_id=user_id,
        expected_proposal_hash=canonical_proposal_hash(tool_name, arguments),
    )
    if not ok:
        raise PermissionDeniedError(
            f"Mutation tool '{tool_name}' rejected: approval token is invalid, expired, or unverified."
        )


__all__ = [
    "APPROVAL_TOKEN_PREFIX",
    "ConsumedTokenStore",
    "STALE_CHECK_ETAG_TOOLS",
    "STALE_CHECK_FINGERPRINT_TOOLS",
    "STALE_CHECK_VERSION_TOOLS",
    "approval_id_without_token",
    "approval_token_bound_tool",
    "canonical_proposal_hash",
    "current_target_fingerprint_from_arguments",
    "execution_token_for_request",
    "expected_target_fingerprint_from_arguments",
    "generate_approval_token",
    "is_signed_approval_token",
    "require_mutation_approval",
    "resolve_approval_token",
    "verify_approval_token",
    "verify_approval_token_sync",
]
