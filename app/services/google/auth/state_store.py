"""Google OAuth state store contracts and implementations (in-memory and Redis)."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.domain.errors import AuthenticationError

from .tokens import (
    Clock,
    GoogleScopeValidator,
    _as_utc,
    utc_now,
)


def _code_challenge(verifier: str) -> str:
    """Create the RFC 7636 S256 PKCE challenge for an OAuth state record."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass(frozen=True, slots=True)
class OAuthStateRecord:
    """Opaque pending OAuth request data kept server-side."""

    state: str
    user_id: str
    code_verifier: str
    redirect_uri: str
    scopes: tuple[str, ...]
    issued_at: datetime


class OAuthStateStore(Protocol):
    """Storage contract for single-use OAuth state records."""

    async def issue(
        self, user_id: str, redirect_uri: str, scopes: Iterable[str]
    ) -> OAuthStateRecord: ...

    async def consume(self, state: str, user_id: str) -> OAuthStateRecord: ...


class InMemoryOAuthStateStore:
    """Single-use OAuth state store for local development and deterministic tests.

    A multi-worker deployment can provide a Redis-backed implementation of the same
    protocol without changing the OAuth service. State is never encoded into a URL
    in a way that exposes the local user ID or PKCE verifier.
    """

    def __init__(self, ttl_seconds: int = 600, clock: Clock = utc_now) -> None:
        if ttl_seconds <= 0:
            raise ValueError("OAuth state TTL must be positive.")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._states: dict[str, OAuthStateRecord] = {}

    async def issue(
        self, user_id: str, redirect_uri: str, scopes: Iterable[str]
    ) -> OAuthStateRecord:
        now = _as_utc(self._clock())
        self._purge(now)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        record = OAuthStateRecord(
            state=state,
            user_id=user_id,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            scopes=tuple(GoogleScopeValidator.normalize(scopes)),
            issued_at=now,
        )
        self._states[state] = record
        return record

    async def consume(self, state: str, user_id: str) -> OAuthStateRecord:
        now = _as_utc(self._clock())
        self._purge(now)
        record = self._states.pop(state, None)
        if record is None or record.user_id != user_id:
            raise AuthenticationError("Invalid or expired Google OAuth state.")
        if now - record.issued_at > self._ttl:
            raise AuthenticationError("Invalid or expired Google OAuth state.")
        return record

    def _purge(self, now: datetime) -> None:
        expired = [
            state for state, record in self._states.items() if now - record.issued_at > self._ttl
        ]
        for state in expired:
            self._states.pop(state, None)


class RedisOAuthStateStore(OAuthStateStore):
    """Redis-backed single-use OAuth state store for multi-worker deployments.

    State survives process restarts and is shared across workers. Consumption
    is atomic (GETDEL semantics) so a callback can never be replayed twice.
    """

    def __init__(
        self,
        client_factory: Callable[[], Awaitable[Any]],
        *,
        ttl_seconds: int = 600,
        clock: Clock = utc_now,
        key_prefix: str | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("OAuth state TTL must be positive.")
        self._client_factory = client_factory
        self._ttl_seconds = ttl_seconds
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._key_prefix = key_prefix

    def _key(self, state: str) -> str:
        if self._key_prefix is not None:
            return f"{self._key_prefix}{state}"
        return f"assistant:oauth-state:{state}"

    async def issue(
        self, user_id: str, redirect_uri: str, scopes: Iterable[str]
    ) -> OAuthStateRecord:
        now = _as_utc(self._clock())
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        record = OAuthStateRecord(
            state=state,
            user_id=user_id,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            scopes=tuple(GoogleScopeValidator.normalize(scopes)),
            issued_at=now,
        )
        payload = {
            "user_id": record.user_id,
            "code_verifier": record.code_verifier,
            "redirect_uri": record.redirect_uri,
            "scopes": list(record.scopes),
            "issued_at": record.issued_at.isoformat(),
        }
        client = await self._client_factory()
        await client.set(self._key(state), json.dumps(payload), ex=self._ttl_seconds)
        return record

    async def consume(self, state: str, user_id: str) -> OAuthStateRecord:
        client = await self._client_factory()
        raw = await client.getdel(self._key(state))
        if not raw:
            raise AuthenticationError("Invalid or expired Google OAuth state.")
        try:
            payload = json.loads(raw)
            record = OAuthStateRecord(
                state=state,
                user_id=str(payload["user_id"]),
                code_verifier=str(payload["code_verifier"]),
                redirect_uri=str(payload["redirect_uri"]),
                scopes=tuple(GoogleScopeValidator.normalize(payload.get("scopes", []))),
                issued_at=datetime.fromisoformat(str(payload["issued_at"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError("Invalid or expired Google OAuth state.") from exc
        if record.user_id != user_id:
            raise AuthenticationError("Invalid or expired Google OAuth state.")
        if _as_utc(self._clock()) - _as_utc(record.issued_at) > self._ttl:
            raise AuthenticationError("Invalid or expired Google OAuth state.")
        return record


__all__ = [
    "InMemoryOAuthStateStore",
    "OAuthStateRecord",
    "OAuthStateStore",
    "RedisOAuthStateStore",
    "_code_challenge",
]
