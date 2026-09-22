"""Google OAuth foundation: state, token lifecycle, scope checks, and client creation."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import GoogleOAuthSettings
from app.core.security import FernetTokenCipher, TokenEncryptionError
from app.domain.errors import AuthenticationError, ConfigurationError, ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError
from app.infrastructure.db.models import GoogleIntegration, User

from .client import (
    AsyncHttpTransport,
    GoogleApiClient,
    GoogleClientFactory,
    GoogleOAuthClient,
)
from .state_store import (
    InMemoryOAuthStateStore,
    OAuthStateRecord,
    OAuthStateStore,
    RedisOAuthStateStore,
    _code_challenge,
)
from .tokens import (
    Clock,
    GoogleIntegrationStatus,
    GoogleScopeValidator,
    GoogleTokenSet,
    _as_utc,
    utc_now,
)

logger = structlog.get_logger(__name__)


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
        effective_user_id = record.user_id if record.user_id and record.user_id != "default-user" else user_id
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

        integration = await self._get_integration(session, effective_user_id)
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
                user_id=effective_user_id,
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

        await self._ensure_user(session, effective_user_id, email)
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
