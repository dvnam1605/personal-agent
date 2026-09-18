"""Google HTTP and OAuth client abstractions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from app.core.config import GoogleOAuthSettings
from app.core.config import settings as app_settings
from app.domain.errors import AuthenticationError, ConfigurationError, ExternalServiceError

from .tokens import (
    Clock,
    GoogleScopeValidator,
    GoogleTokenSet,
    utc_now,
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


__all__ = [
    "AsyncHttpTransport",
    "GoogleApiClient",
    "GoogleClientFactory",
    "GoogleOAuthClient",
]
