"""Shared deterministic HTTP behavior for Google resource adapters."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.domain.errors import (
    AuthenticationError,
    ExternalServiceError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ValidationError,
)
from app.services.google_auth import GoogleApiClient

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry policy for transient Google API failures."""

    max_attempts: int = 3
    initial_delay_seconds: float = 0.05
    max_delay_seconds: float = 1.0
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({408, 429, 500, 502, 503, 504})
    )

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("RetryPolicy.max_attempts must be at least one.")
        if self.initial_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("Retry delays cannot be negative.")
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max_delay_seconds cannot be less than initial_delay_seconds.")

    def delay_for_retry(self, retry_number: int, response: httpx.Response | None = None) -> float:
        """Return a bounded delay, honoring a safe numeric Retry-After header."""
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(self.max_delay_seconds, max(0.0, float(retry_after)))
                except ValueError:
                    pass
        return min(
            self.max_delay_seconds,
            self.initial_delay_seconds * (2 ** max(0, retry_number - 1)),
        )


class GoogleResourceAdapter:
    """Low-level adapter helper with normalized errors and bounded retries."""

    def __init__(
        self,
        client: GoogleApiClient,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.client = client
        self.retry_policy = retry_policy or RetryPolicy()
        self.sleep = sleep
        self.last_retry_count = 0

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        allow_empty: bool = False,
        retryable: bool | None = None,
    ) -> Any:
        """Make one bounded request and return only provider JSON data."""
        url = self._url(path)
        self.last_retry_count = 0
        can_retry = (
            retryable
            if retryable is not None
            else method.upper() in {"GET", "HEAD", "PUT", "DELETE"}
        )
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                response = await self.client.request(
                    method,
                    url,
                    params=dict(params) if params else None,
                    json=json,
                )
            except (httpx.HTTPError, OSError) as exc:
                if can_retry and attempt < self.retry_policy.max_attempts:
                    self.last_retry_count = attempt
                    await self.sleep(self.retry_policy.delay_for_retry(attempt))
                    continue
                raise ExternalServiceError(
                    "Google API request failed.",
                    service_name="google",
                    details={"operation": operation, "retry_count": attempt - 1},
                ) from exc

            if (
                can_retry
                and
                response.status_code in self.retry_policy.retry_statuses
                and attempt < self.retry_policy.max_attempts
            ):
                self.last_retry_count = attempt
                await self.sleep(self.retry_policy.delay_for_retry(attempt, response))
                continue

            if not 200 <= response.status_code < 300:
                self._raise_provider_error(response, operation, attempt - 1)

            if response.status_code in (204, 205):
                if allow_empty:
                    return None
                raise ExternalServiceError(
                    f"Google returned an empty response for {operation}.",
                    service_name="google",
                    details={"operation": operation, "status_code": response.status_code},
                )

            try:
                payload = response.json()
            except (TypeError, ValueError) as exc:
                raise ExternalServiceError(
                    f"Google returned invalid JSON for {operation}.",
                    service_name="google",
                    details={"operation": operation, "status_code": response.status_code},
                ) from exc
            return payload

        # The loop always returns or raises; this protects type checkers if the
        # retry policy is changed in the future.
        raise ExternalServiceError(
            "Google API request failed.",
            service_name="google",
            details={"operation": operation},
        )

    def _url(self, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{self.client.base_url.rstrip('/')}{normalized_path}"

    @staticmethod
    def _raise_provider_error(
        response: httpx.Response,
        operation: str,
        retry_count: int,
    ) -> None:
        details = {
            "operation": operation,
            "status_code": response.status_code,
            "retry_count": retry_count,
        }
        if response.status_code == 401:
            raise AuthenticationError(
                "Google authentication is invalid or expired.", details=details
            )
        if response.status_code == 403:
            raise PermissionDeniedError("Google denied access to this resource.", details=details)
        if response.status_code == 404:
            raise NotFoundError("Google resource was not found.", details=details)
        if response.status_code == 429:
            raise RateLimitError("Google rate limit reached.", details=details)
        if 400 <= response.status_code < 500:
            raise ValidationError("Google rejected the request.", details=details)
        raise ExternalServiceError(
            "Google API returned a server error.",
            service_name="google",
            details=details,
        )


def require_object(payload: Any, operation: str) -> dict[str, Any]:
    """Validate a provider response shape without returning raw provider data."""
    if not isinstance(payload, dict):
        raise ExternalServiceError(
            f"Google returned an invalid response for {operation}.",
            service_name="google",
            details={"operation": operation},
        )
    return payload


def require_list(payload: Any, key: str, operation: str) -> list[Any]:
    """Read a list field from a provider object with a normalized error."""
    data = require_object(payload, operation)
    values = data.get(key, [])
    if not isinstance(values, list):
        raise ExternalServiceError(
            f"Google returned an invalid response for {operation}.",
            service_name="google",
            details={"operation": operation},
        )
    return values


__all__ = ["GoogleResourceAdapter", "RetryPolicy", "require_list", "require_object"]
