"""Infrastructure settings: database, Redis, LangSmith tracing and HTTP-boundary security."""

import ipaddress

from pydantic import BaseModel, Field, field_validator


class DatabaseSettings(BaseModel):
    url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5434/assistant",
        description="PostgreSQL connection URL. Port 5434 is this project's dedicated instance; 5432 may belong to an unrelated local database.",
    )
    pool_size: int = Field(default=10, description="Connection pool size")
    max_overflow: int = Field(default=20, description="Max overflow connections")
    echo: bool = Field(default=False, description="Echo SQL queries")


class RedisSettings(BaseModel):
    url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )
    session_ttl_seconds: int = Field(default=86400, description="Session state TTL")
    cache_ttl_seconds: int = Field(default=3600, description="General cache TTL")
    socket_timeout_seconds: float = Field(default=2.0, description="Redis socket timeout")


class LangSmithSettings(BaseModel):
    tracing_enabled: bool = Field(default=False, description="Enable LangSmith tracing")
    endpoint: str = Field(
        default="https://api.smith.langchain.com", description="LangSmith API Endpoint"
    )
    api_key: str | None = Field(default=None, description="LangSmith API Key")
    project: str = Field(default="personal-ai-assistant", description="LangSmith Project Name")


class SecuritySettings(BaseModel):
    """Deployment-safety controls enforced at the HTTP boundary."""

    api_key: str | None = Field(
        default=None,
        repr=False,
        exclude=True,
        description=(
            "Shared API key required on every authenticated request (X-API-Key). "
            "Unset keys fail closed outside development/testing."
        ),
    )
    api_key_user_id: str | None = Field(
        default=None,
        description=(
            "User id bound to the shared API key. Required in staging/production so "
            "X-User-ID cannot impersonate another tenant (H3)."
        ),
    )
    approval_signing_key: str | None = Field(
        default=None,
        repr=False,
        exclude=True,
        description=(
            "Dedicated HMAC-SHA256 key for signing approval tokens (≥32 bytes). "
            "Must be set in staging/production; dev/test falls back to a "
            "deterministic test-only key with a log warning."
        ),
    )
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        description="Explicit CORS origin allowlist; never use '*' together with credentials.",
    )
    trusted_proxies: list[str] = Field(
        default_factory=list,
        description=(
            "Addresses or CIDR ranges of reverse proxies (e.g. the nginx container network) "
            "whose X-Forwarded-For header is honoured when deriving the client address for "
            "rate limits. Empty: forwarding headers are ignored and the peer address is used."
        ),
    )

    @field_validator("trusted_proxies")
    @classmethod
    def validate_trusted_proxies(cls, value: list[str]) -> list[str]:
        """Reject malformed entries at startup instead of silently trusting nothing."""
        for entry in value:
            ipaddress.ip_network(entry, strict=False)
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def reject_wildcard_cors(cls, value: list[str]) -> list[str]:
        """M10: never allow a wildcard origin, even with credentials disabled."""
        cleaned = [origin.strip() for origin in value if origin and origin.strip()]
        if any(origin == "*" for origin in cleaned):
            raise ValueError("CORS allowlist must not include '*'.")
        return cleaned
