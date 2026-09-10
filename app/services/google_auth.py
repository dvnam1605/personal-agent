"""Google OAuth foundation: state, token lifecycle, scope checks, and client creation."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import GoogleOAuthSettings
from app.core.config import settings as app_settings
from app.core.security import FernetTokenCipher, TokenEncryptionError
from app.domain.errors import AuthenticationError, ConfigurationError, ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.infrastructure.db.models import GoogleIntegration, User

logger = structlog.get_logger(__name__)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize timestamps received from providers or database drivers."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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


class GoogleScopeValidator:
    """Validate Google OAuth grants without treating a narrower scope as broader."""

    _SCOPE_COVERAGE: dict[str, frozenset[str]] = {
        "https://www.googleapis.com/auth/calendar": frozenset(
            {
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/calendar.readonly",
            }
        ),
        "https://www.googleapis.com/auth/drive": frozenset(
            {
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/drive.readonly",
                "https://www.googleapis.com/auth/drive.file",
            }
        ),
        "https://www.googleapis.com/auth/drive.file": frozenset(
            {
                "https://www.googleapis.com/auth/drive.file",
            }
        ),
    }

    @staticmethod
    def normalize(scopes: Iterable[str] | str) -> list[str]:
        values = scopes.split() if isinstance(scopes, str) else scopes
        normalized: list[str] = []
        for scope in values:
            if not isinstance(scope, str):
                continue
            value = scope.strip()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    @classmethod
    def missing(
        cls, granted_scopes: Iterable[str] | str, required_scopes: Iterable[str] | str
    ) -> list[str]:
        granted = set(cls.normalize(granted_scopes))
        missing: list[str] = []
        for scope in cls.normalize(required_scopes):
            if scope in granted or any(
                scope in cls._SCOPE_COVERAGE.get(granted_scope, frozenset())
                for granted_scope in granted
            ):
                continue
            missing.append(scope)
        return missing

    @classmethod
    def require(
        cls, granted_scopes: Iterable[str] | str, required_scopes: Iterable[str] | str
    ) -> None:
        missing = cls.missing(granted_scopes, required_scopes)
        if missing:
            raise DomainValidationError(
                "Google authorization is missing required scopes.",
                details={"missing_scopes": missing},
            )


class GoogleTokenSet(BaseModel):
    """Provider token response with plaintext fields kept out of repr/serialization logs."""

    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(..., min_length=1, repr=False, exclude=True)
    refresh_token: str | None = Field(default=None, min_length=1, repr=False, exclude=True)
    token_type: str = Field(default="Bearer", min_length=1)
    expires_at: datetime
    scopes: list[str] = Field(default_factory=list)

    @field_validator("access_token", "refresh_token", mode="before")
    @classmethod
    def reject_blank_tokens(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return GoogleScopeValidator.normalize(value)
        if isinstance(value, list | tuple | set):
            return GoogleScopeValidator.normalize(value)
        return []

    @model_validator(mode="after")
    def normalize_expiry(self) -> GoogleTokenSet:
        self.expires_at = _as_utc(self.expires_at)
        return self

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        requested_scopes: Iterable[str],
        clock: Clock = utc_now,
        fallback_to_requested_scopes: bool = True,
    ) -> GoogleTokenSet:
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise ExternalServiceError(
                "Google did not return an access token.", service_name="google"
            )
        raw_expires = payload.get("expires_in", 3600)
        try:
            expires_in = max(0, int(raw_expires))
        except (TypeError, ValueError) as exc:
            raise ExternalServiceError(
                "Google returned an invalid token lifetime.", service_name="google"
            ) from exc
        raw_scopes = payload.get("scope")
        scopes = GoogleScopeValidator.normalize(raw_scopes) if raw_scopes else []
        if not scopes and fallback_to_requested_scopes:
            scopes = GoogleScopeValidator.normalize(requested_scopes)
        refresh_token = payload.get("refresh_token")
        return cls(
            access_token=access_token.strip(),
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            token_type=str(payload.get("token_type") or "Bearer"),
            expires_at=_as_utc(clock()) + timedelta(seconds=expires_in),
            scopes=scopes,
        )


class AsyncHttpTransport(Protocol):
    """Minimal async HTTP shape used by the Google client and its tests."""

    async def post(self, url: str, **kwargs: Any) -> httpx.Response: ...

    async def get(self, url: str, **kwargs: Any) -> httpx.Response: ...


class GoogleOAuthClient:
    """Small provider adapter for OAuth and OIDC calls, with no provider SDK coupling."""

    def __init__(
        self,
        settings: GoogleOAuthSettings,
        transport: AsyncHttpTransport | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.clock = clock

    def authorization_url(
        self,
        state: str,
        code_challenge: str,
        redirect_uri: str,
        scopes: Iterable[str],
    ) -> str:
        """Build a Google authorization-code URL with offline access and PKCE."""
        self._require_credentials()
        query = urlencode(
            {
                "client_id": self.settings.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(GoogleScopeValidator.normalize(scopes)),
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if "?" in self.settings.authorization_endpoint else "?"
        return f"{self.settings.authorization_endpoint}{separator}{query}"

    async def exchange_code(
        self, code: str, code_verifier: str, redirect_uri: str, scopes: Iterable[str]
    ) -> GoogleTokenSet:
        """Exchange a one-time authorization code for access and refresh tokens."""
        self._require_credentials()
        response = await self._post(
            self.settings.token_endpoint,
            data={
                "code": code,
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
        )
        payload = self._provider_payload(response, operation="authorization code exchange")
        return GoogleTokenSet.from_payload(
            payload,
            scopes,
            clock=self.clock,
            fallback_to_requested_scopes=False,
        )

    async def refresh_access_token(
        self, refresh_token: str, scopes: Iterable[str]
    ) -> GoogleTokenSet:
        """Refresh an access token without persisting or returning the refresh token to callers."""
        self._require_credentials()
        response = await self._post(
            self.settings.token_endpoint,
            data={
                "refresh_token": refresh_token,
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "grant_type": "refresh_token",
            },
        )
        payload = self._provider_payload(response, operation="access token refresh")
        return GoogleTokenSet.from_payload(
            payload,
            scopes,
            clock=self.clock,
            fallback_to_requested_scopes=True,
        )

    async def revoke_token(self, token: str) -> None:
        """Revoke a Google token; an already-invalid token is treated as disconnected."""
        response = await self._post(
            self.settings.revoke_endpoint,
            params={"token": token},
        )
        if response.status_code in (200, 204):
            return
        if response.status_code == 400:
            try:
                payload = response.json()
            except (ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if not payload or payload.get("error") == "invalid_token":
                return
        self._provider_payload(response, operation="token revocation")

    async def userinfo(self, access_token: str) -> dict[str, str]:
        """Read only non-secret account identity fields for connection status."""
        response = await self._get(
            self.settings.userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        payload = self._provider_payload(response, operation="userinfo lookup")
        return {
            key: value
            for key in ("sub", "email")
            if isinstance(value := payload.get(key), str) and value.strip()
        }

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.transport is not None:
            try:
                return await self.transport.post(url, **kwargs)
            except (httpx.HTTPError, OSError) as exc:
                raise ExternalServiceError(
                    "Google OAuth request failed.", service_name="google"
                ) from exc
        try:
            async with httpx.AsyncClient(
                timeout=app_settings.timeouts.google_api_seconds
            ) as client:
                return await client.post(url, **kwargs)
        except (httpx.HTTPError, OSError) as exc:
            raise ExternalServiceError(
                "Google OAuth request failed.", service_name="google"
            ) from exc

    async def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        if self.transport is not None:
            try:
                return await self.transport.get(url, **kwargs)
            except (httpx.HTTPError, OSError) as exc:
                raise ExternalServiceError(
                    "Google OAuth request failed.", service_name="google"
                ) from exc
        try:
            async with httpx.AsyncClient(
                timeout=app_settings.timeouts.google_api_seconds
            ) as client:
                return await client.get(url, **kwargs)
        except (httpx.HTTPError, OSError) as exc:
            raise ExternalServiceError(
                "Google OAuth request failed.", service_name="google"
            ) from exc

    @staticmethod
    def _provider_payload(response: httpx.Response, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            if response.is_success:
                raise ExternalServiceError(
                    f"Google returned an invalid response for {operation}.",
                    service_name="google",
                )
            payload = {}
        if not response.is_success:
            if payload.get("error") == "invalid_grant":
                raise AuthenticationError("Google authorization is no longer valid.")
            raise ExternalServiceError(
                f"Google {operation} failed.",
                service_name="google",
                details={"status_code": response.status_code},
            )
        return payload

    def _require_credentials(self) -> None:
        if not self.settings.client_id or not self.settings.client_secret:
            raise ConfigurationError(
                "Google OAuth credentials are not configured.",
                details={"client_secrets_file_configured": bool(self.settings.client_secrets_file)},
            )


@dataclass(slots=True)
class GoogleApiClient:
    """Authorized common client handed to later Gmail/Calendar/Drive adapters."""

    _access_token: str = field(repr=False)
    base_url: str = "https://www.googleapis.com"
    transport: AsyncHttpTransport | None = None

    @property
    def authorization_headers(self) -> dict[str, str]:
        """Return an authorization header without exposing the token in public models."""
        return {"Authorization": f"Bearer {self._access_token}"}

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Issue an authorized request for a future deterministic Google adapter."""
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(self.authorization_headers)
        if self.transport is not None:
            request_method = getattr(self.transport, method.lower(), None)
            if request_method is None:
                raise ExternalServiceError("Unsupported Google API method.", service_name="google")
            return await request_method(url, headers=headers, **kwargs)
        async with httpx.AsyncClient(timeout=app_settings.timeouts.google_api_seconds) as client:
            return await client.request(method, url, headers=headers, **kwargs)


class GoogleClientFactory:
    """Create common authorized clients after the OAuth service validates scopes."""

    @staticmethod
    def create(
        access_token: str,
        transport: AsyncHttpTransport | None = None,
        base_url: str = "https://www.googleapis.com",
    ) -> GoogleApiClient:
        if not access_token or not access_token.strip():
            raise AuthenticationError("A valid Google access token is required.")
        return GoogleApiClient(_access_token=access_token, base_url=base_url, transport=transport)


class GoogleIntegrationStatus(BaseModel):
    """Safe connection status; never contains OAuth token material."""

    model_config = ConfigDict(extra="forbid")

    connected: bool
    healthy: bool
    email: str | None = None
    scopes: list[str] = Field(default_factory=list)
    missing_scopes: list[str] = Field(default_factory=list)
    access_token_expires_at: datetime | None = None
    revoked_at: datetime | None = None


class GoogleOAuthService:
    """Persist and operate a user's Google OAuth connection."""

    def __init__(
        self,
        settings: GoogleOAuthSettings,
        client: GoogleOAuthClient | None = None,
        state_store: OAuthStateStore | None = None,
        cipher: FernetTokenCipher | None = None,
        clock: Clock = utc_now,
        client_transport: AsyncHttpTransport | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.client = client or GoogleOAuthClient(settings, transport=client_transport, clock=clock)
        self.state_store = state_store or InMemoryOAuthStateStore(
            ttl_seconds=settings.oauth_state_ttl_seconds, clock=clock
        )
        self._cipher = cipher

    async def start(self, user_id: str) -> str:
        """Create state and return the provider authorization URL."""
        self._validate_configuration()
        record = await self.state_store.issue(
            user_id=user_id,
            redirect_uri=self.settings.redirect_uri,
            scopes=self.settings.scopes,
        )
        return self.client.authorization_url(
            state=record.state,
            code_challenge=_code_challenge(record.code_verifier),
            redirect_uri=record.redirect_uri,
            scopes=record.scopes,
        )

    async def callback(
        self,
        session: AsyncSession,
        user_id: str,
        code: str | None,
        state: str,
        error: str | None = None,
    ) -> GoogleIntegrationStatus:
        """Consume OAuth state, exchange the code, validate scopes, and persist encrypted tokens."""
        record = await self.state_store.consume(state, user_id)
        if error:
            raise AuthenticationError("Google authorization was denied.")
        if not code or not code.strip():
            raise DomainValidationError("Google authorization callback did not include a code.")

        self._validate_configuration()
        token_set = await self.client.exchange_code(
            code=code,
            code_verifier=record.code_verifier,
            redirect_uri=record.redirect_uri,
            scopes=record.scopes,
        )
        GoogleScopeValidator.require(token_set.scopes, record.scopes)

        integration = await self._get_integration(session, user_id)
        refresh_token = token_set.refresh_token
        if not refresh_token and integration is None:
            raise AuthenticationError(
                "Google did not return a refresh token. Revoke the app access and authorize again."
            )

        email: str | None = None
        google_subject: str | None = None
        try:
            profile = await self.client.userinfo(token_set.access_token)
            email = profile.get("email")
            google_subject = profile.get("sub")
        except ExternalServiceError:
            # Connection establishment remains valid if the optional profile lookup is unavailable.
            pass

        cipher = self._get_cipher()
        if integration is None:
            if not refresh_token:
                raise AuthenticationError(
                    "Google did not return a refresh token. Revoke the app access and authorize again."
                )
            integration = GoogleIntegration(
                user_id=user_id,
                refresh_token_encrypted=cipher.encrypt(refresh_token),
            )
            session.add(integration)
        elif refresh_token:
            integration.refresh_token_encrypted = cipher.encrypt(refresh_token)

        integration.access_token_encrypted = cipher.encrypt(token_set.access_token)
        integration.token_type = token_set.token_type
        integration.scopes = list(token_set.scopes)
        integration.access_token_expires_at = token_set.expires_at
        integration.last_refreshed_at = _as_utc(self.clock())
        integration.revoked_at = None
        if email:
            integration.email = email
        if google_subject:
            integration.google_subject = google_subject

        await self._ensure_user(session, user_id, email)
        await session.flush()
        return self._status_from_integration(integration)

    async def status(self, session: AsyncSession, user_id: str) -> GoogleIntegrationStatus:
        """Return safe local connection health and missing-scope information."""
        integration = await self._get_integration(session, user_id)
        if integration is None:
            return GoogleIntegrationStatus(
                connected=False,
                healthy=False,
                missing_scopes=GoogleScopeValidator.normalize(self.settings.scopes),
            )
        return self._status_from_integration(integration)

    async def get_access_token(
        self,
        session: AsyncSession,
        user_id: str,
        required_scopes: Iterable[str] | None = None,
    ) -> str:
        """Return a valid access token, refreshing and re-encrypting it when needed."""
        self._validate_configuration()
        integration = await self._get_integration(session, user_id)
        if integration is None or integration.revoked_at is not None:
            raise AuthenticationError("Google is not connected.")

        required = GoogleScopeValidator.normalize(required_scopes or self.settings.scopes)
        GoogleScopeValidator.require(integration.scopes, required)
        now = _as_utc(self.clock())
        if (
            integration.access_token_encrypted
            and integration.access_token_expires_at
            and _as_utc(integration.access_token_expires_at)
            > now + timedelta(seconds=self.settings.refresh_skew_seconds)
        ):
            return self._decrypt(integration.access_token_encrypted)

        refresh_token = self._decrypt(integration.refresh_token_encrypted)
        try:
            token_set = await self.client.refresh_access_token(refresh_token, integration.scopes)
        except AuthenticationError:
            integration.revoked_at = now
            await session.flush()
            raise
        GoogleScopeValidator.require(token_set.scopes, required)
        cipher = self._get_cipher()
        integration.access_token_encrypted = cipher.encrypt(token_set.access_token)
        integration.access_token_expires_at = token_set.expires_at
        integration.last_refreshed_at = now
        if token_set.refresh_token:
            integration.refresh_token_encrypted = cipher.encrypt(token_set.refresh_token)
        await session.flush()
        return token_set.access_token

    async def create_client(
        self,
        session: AsyncSession,
        user_id: str,
        required_scopes: Iterable[str] | None = None,
        transport: AsyncHttpTransport | None = None,
    ) -> GoogleApiClient:
        """Refresh if necessary, then create the common authorized API client."""
        access_token = await self.get_access_token(session, user_id, required_scopes)
        return GoogleClientFactory.create(access_token, transport=transport)

    async def disconnect(self, session: AsyncSession, user_id: str) -> bool:
        """Revoke both refresh and access tokens and remove the local encrypted connection.

        Fail-open on external revocation failures (e.g. transient Google 5xx or timeouts)
        to prevent users from being permanently locked into a connected state.
        """
        integration = await self._get_integration(session, user_id)
        if integration is None:
            return False
        if integration.refresh_token_encrypted:
            refresh_token = self._decrypt(integration.refresh_token_encrypted)
            try:
                await self.client.revoke_token(refresh_token)
            except Exception as exc:  # noqa: BLE001 - Google revoke is fail-open by contract
                logger.warning(
                    "google_refresh_token_revoke_failed_fail_open",
                    extra={"error": str(exc), "user_id": user_id},
                )
        if integration.access_token_encrypted:
            access_token = self._decrypt(integration.access_token_encrypted)
            try:
                await self.client.revoke_token(access_token)
            except Exception as exc:  # noqa: BLE001 - Google revoke is fail-open by contract
                logger.warning(
                    "google_access_token_revoke_failed_fail_open",
                    extra={"error": str(exc), "user_id": user_id},
                )
        await session.delete(integration)
        await session.flush()
        return True

    def _validate_configuration(self) -> None:
        if not self.settings.client_id or not self.settings.client_secret:
            raise ConfigurationError(
                "Google OAuth credentials are not configured.",
                details={"client_secrets_file_configured": bool(self.settings.client_secrets_file)},
            )
        if not self.settings.redirect_uri.strip():
            raise ConfigurationError("Google OAuth redirect URI is not configured.")
        if not GoogleScopeValidator.normalize(self.settings.scopes):
            raise ConfigurationError("At least one Google OAuth scope is required.")

    def _get_cipher(self) -> FernetTokenCipher:
        if self._cipher is not None:
            return self._cipher
        if self.settings.token_encryption_key:
            self._cipher = FernetTokenCipher(self.settings.token_encryption_key)
        else:
            key_path = Path(self.settings.token_encryption_key_file)
            self._cipher = FernetTokenCipher.from_key_file(key_path)
        return self._cipher

    def _decrypt(self, ciphertext: str) -> str:
        try:
            return self._get_cipher().decrypt(ciphertext)
        except TokenEncryptionError as exc:
            raise AuthenticationError(
                "Stored Google credentials are unavailable; reconnect Google."
            ) from exc

    @staticmethod
    async def _get_integration(session: AsyncSession, user_id: str) -> GoogleIntegration | None:
        result = await session.execute(
            select(GoogleIntegration).where(GoogleIntegration.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _ensure_user(session: AsyncSession, user_id: str, email: str | None) -> User:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(id=user_id, email=email or f"{user_id}@local.invalid")
            session.add(user)
            await session.flush()
        elif email and user.email.endswith("@local.invalid"):
            user.email = email
        return user

    def _status_from_integration(self, integration: GoogleIntegration) -> GoogleIntegrationStatus:
        scopes = GoogleScopeValidator.normalize(integration.scopes)
        missing = GoogleScopeValidator.missing(scopes, self.settings.scopes)
        connected = integration.revoked_at is None
        token_material_ok = bool(integration.refresh_token_encrypted)
        if token_material_ok:
            try:
                self._decrypt(integration.refresh_token_encrypted)
            except AuthenticationError:
                token_material_ok = False
        healthy = connected and not missing and token_material_ok
        return GoogleIntegrationStatus(
            connected=connected,
            healthy=healthy,
            email=integration.email,
            scopes=scopes,
            missing_scopes=missing,
            access_token_expires_at=integration.access_token_expires_at,
            revoked_at=integration.revoked_at,
        )


__all__ = [
    "GoogleApiClient",
    "GoogleClientFactory",
    "GoogleIntegrationStatus",
    "GoogleOAuthClient",
    "GoogleOAuthService",
    "GoogleScopeValidator",
    "GoogleTokenSet",
    "InMemoryOAuthStateStore",
    "OAuthStateRecord",
    "OAuthStateStore",
    "RedisOAuthStateStore",
]
