"""Google OAuth token representations, scope validators, and integration status models."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.errors import ExternalServiceError
from app.domain.errors import ValidationError as DomainValidationError

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize timestamps received from providers or database drivers."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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


__all__ = [
    "Clock",
    "GoogleIntegrationStatus",
    "GoogleScopeValidator",
    "GoogleTokenSet",
    "_as_utc",
    "utc_now",
]
