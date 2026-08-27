"""Typed application settings using Pydantic Settings."""

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root (app/core/config.py -> app/core -> app -> root).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_project_path(value: str | Path) -> Path:
    """Resolve a configured path relative to the project root, not the process CWD."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


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


class LLMProviderSettings(BaseModel):
    openai_api_key: str | None = Field(default=None, description="OpenAI API Key")
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API Key")
    gemini_api_key: str | None = Field(default=None, description="Google Gemini API Key")
    primary_model: str = Field(default="gpt-4o", description="Primary reasoning model")
    fast_model: str = Field(default="gpt-4o-mini", description="Fast classifier model")
    temperature: float = Field(default=0.0, description="Default sampling temperature")


class GoogleOAuthSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_id: str | None = Field(default=None, repr=False, description="Google OAuth Client ID")
    client_secret: str | None = Field(
        default=None, repr=False, exclude=True, description="Google OAuth Client Secret"
    )
    client_secrets_file: str | None = Field(
        default=None,
        description="Path to the downloaded Google OAuth client-secrets JSON file",
    )
    redirect_uri: str = Field(
        default="http://localhost:8000/auth/google/callback",
        description="OAuth redirect URI",
    )
    scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/contacts.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
        description="OAuth requested scopes",
    )
    authorization_endpoint: str = Field(
        default="https://accounts.google.com/o/oauth2/v2/auth",
        description="Google OAuth authorization endpoint",
    )
    token_endpoint: str = Field(
        default="https://oauth2.googleapis.com/token",
        description="Google OAuth token endpoint",
    )
    revoke_endpoint: str = Field(
        default="https://oauth2.googleapis.com/revoke",
        description="Google OAuth token revocation endpoint",
    )
    userinfo_endpoint: str = Field(
        default="https://openidconnect.googleapis.com/v1/userinfo",
        description="Google OpenID Connect userinfo endpoint",
    )
    token_encryption_key: str | None = Field(
        default=None,
        repr=False,
        exclude=True,
        description="Optional Fernet key for encrypting Google tokens",
    )
    token_encryption_key_file: str = Field(
        default=".secrets/google_token.key",
        description="Local key file used when token_encryption_key is not supplied",
    )
    oauth_state_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=900,
        description="Maximum lifetime of a pending OAuth state",
    )
    refresh_skew_seconds: int = Field(
        default=60,
        ge=0,
        le=3600,
        description="Refresh an access token this many seconds before expiry",
    )

    @field_validator("client_id", "client_secret", "client_secrets_file", mode="before")
    @classmethod
    def empty_strings_are_none(cls, value: object) -> object:
        """Treat blank dotenv values as unset so the JSON file can supply credentials."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def load_client_secrets_file(self) -> "GoogleOAuthSettings":
        """Load missing client credentials from Google's downloaded JSON configuration."""
        if self.client_id and self.client_secret:
            return self
        if not self.client_secrets_file:
            return self

        path = resolve_project_path(self.client_secrets_file)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            client = payload.get("web") or payload.get("installed")
            if not isinstance(client, dict):
                return self
            client_id = client.get("client_id")
            client_secret = client.get("client_secret")
            updates: dict[str, str] = {}
            if not self.client_id and isinstance(client_id, str) and client_id.strip():
                updates["client_id"] = client_id.strip()
            if not self.client_secret and isinstance(client_secret, str) and client_secret.strip():
                updates["client_secret"] = client_secret.strip()
            if updates:
                for field_name, value in updates.items():
                    setattr(self, field_name, value)
        except (OSError, ValueError, TypeError):
            # Defer the actionable configuration error until an OAuth operation is requested.
            pass
        return self


class LangSmithSettings(BaseModel):
    tracing_enabled: bool = Field(default=False, description="Enable LangSmith tracing")
    endpoint: str = Field(
        default="https://api.smith.langchain.com", description="LangSmith API Endpoint"
    )
    api_key: str | None = Field(default=None, description="LangSmith API Key")
    project: str = Field(default="personal-ai-assistant", description="LangSmith Project Name")


class EmbeddingSettings(BaseModel):
    model: str = Field(
        default="AITeamVN/Vietnamese_Embedding",
        description="Embedding model name (local snapshot by default, ADR 0012)",
    )
    dimensions: int = Field(default=1024, description="Embedding vector dimensions")
    batch_size: int = Field(default=64, description="Embedding generation batch size")
    local_path: str | None = Field(
        default=None,
        description=(
            "Optional local HuggingFace snapshot directory; when set, the offline "
            "model at this path is used instead of a hosted provider."
        ),
    )


class RerankerSettings(BaseModel):
    model: str = Field(
        default="namdp-ptit/ViRanker",
        description="Cross-encoder reranker model (local snapshot by default, ADR 0012)",
    )
    top_k: int = Field(default=5, description="Number of reranked candidates to return")
    threshold: float = Field(default=0.3, description="Minimum relevance score threshold")
    local_path: str | None = Field(
        default=None,
        description=(
            "Optional local HuggingFace snapshot directory; when set, the offline "
            "reranker at this path is used instead of downloading from the hub."
        ),
    )


class ParsingSettings(BaseModel):
    """Parse-quality gating and OCR fallback policy (spec P9B-3/P9B-4)."""

    ocr_fallback_enabled: bool = Field(
        default=False,
        description=(
            "Runtime V1 default OFF: scanned PDFs get typed NEEDS_OCR and are "
            "queued for the offline OCR path (P9E) instead of inline OCR."
        ),
    )
    min_useful_text_length: int = Field(
        default=64,
        description="Minimum extracted text length considered useful",
    )
    large_source_bytes: int = Field(
        default=200_000,
        description="Sources at or above this size with no useful text need OCR",
    )
    garbage_char_ratio: float = Field(
        default=0.25,
        description="Ratio of replacement/non-printable chars that marks a parse corrupt",
    )


class ChunkingSettings(BaseModel):
    """Parent/child chunk budgets and merging policy (spec P9C)."""

    parent_target_tokens: int = Field(
        default=1600,
        description="Target size for PARENT chunks (benchmark start ~1200-2000)",
    )
    parent_hard_max_tokens: int = Field(
        default=2400,
        description="Hard maximum for PARENT chunks before the split ladder kicks in",
    )
    child_target_tokens: int = Field(
        default=500,
        description="Target size for CHILD chunks (benchmark start ~350-650)",
    )
    child_hard_max_tokens: int = Field(
        default=800,
        description="Hard maximum for CHILD chunks; never exceeded by construction",
    )
    merge_small_nodes_below_tokens: int = Field(
        default=40,
        description="Adjacent tiny paragraphs/lists may merge within one parent",
    )


class ReActBudgetSettings(BaseModel):
    max_steps: int = Field(default=6, description="Max reasoning steps in specialist ReAct loop")
    max_tool_calls: int = Field(default=8, description="Max total tool calls per ReAct session")
    timeout_seconds: float = Field(default=30.0, description="ReAct execution timeout in seconds")
    stop_on_no_progress: bool = Field(
        default=True, description="Detect and stop identical consecutive tool calls"
    )


class SupervisorBudgetSettings(BaseModel):
    max_iterations: int = Field(default=5, description="Max task execution iterations")
    max_replans: int = Field(default=2, description="Max allowed replanning attempts")
    max_parallel_tasks: int = Field(default=4, description="Max parallel sub-tasks")


class RouteBudget(BaseModel):
    target_llm_calls: int
    target_latency_seconds: float
    max_tokens: int


class LLMBudgetSettings(BaseModel):
    direct_specialist: RouteBudget = Field(
        default_factory=lambda: RouteBudget(
            target_llm_calls=1, target_latency_seconds=2.0, max_tokens=2000
        )
    )
    specialist_react: RouteBudget = Field(
        default_factory=lambda: RouteBudget(
            target_llm_calls=3, target_latency_seconds=5.0, max_tokens=8000
        )
    )
    known_workflow: RouteBudget = Field(
        default_factory=lambda: RouteBudget(
            target_llm_calls=2, target_latency_seconds=3.5, max_tokens=6000
        )
    )
    supervisor_dag: RouteBudget = Field(
        default_factory=lambda: RouteBudget(
            target_llm_calls=5, target_latency_seconds=10.0, max_tokens=16000
        )
    )


class TimeoutsSettings(BaseModel):
    google_api_seconds: float = Field(default=10.0, description="Google API request timeout")
    web_search_seconds: float = Field(default=8.0, description="Web search provider timeout")
    llm_request_seconds: float = Field(default=30.0, description="LLM provider request timeout")
    database_query_seconds: float = Field(default=5.0, description="Database query timeout")


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
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        description="Explicit CORS origin allowlist; never use '*' together with credentials.",
    )


class Settings(BaseSettings):
    """Main Application Settings."""

    app_name: str = "Personal AI Assistant"
    app_version: str = "0.1.0"
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8000

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    llm: LLMProviderSettings = Field(default_factory=LLMProviderSettings)
    google: GoogleOAuthSettings = Field(default_factory=GoogleOAuthSettings)
    # Backward-compatible flat dotenv names used by the repository's existing .env files.
    google_client_id: str | None = Field(default=None, repr=False, exclude=True)
    google_client_secret: str | None = Field(default=None, repr=False, exclude=True)
    google_client_secrets_file: str | None = Field(default=None, repr=False, exclude=True)
    google_redirect_uri: str | None = Field(default=None, repr=False, exclude=True)
    google_token_encryption_key: str | None = Field(default=None, repr=False, exclude=True)
    google_token_encryption_key_file: str | None = Field(default=None, repr=False, exclude=True)
    langsmith: LangSmithSettings = Field(default_factory=LangSmithSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    parsing: ParsingSettings = Field(default_factory=ParsingSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    react_budget: ReActBudgetSettings = Field(default_factory=ReActBudgetSettings)
    supervisor_budget: SupervisorBudgetSettings = Field(default_factory=SupervisorBudgetSettings)
    llm_budget: LLMBudgetSettings = Field(default_factory=LLMBudgetSettings)
    timeouts: TimeoutsSettings = Field(default_factory=TimeoutsSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)

    @property
    def auth_enforced(self) -> bool:
        """Whether requests must present a valid API key to be served."""
        return self.environment not in (Environment.DEVELOPMENT, Environment.TESTING)

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, v: object) -> bool:
        """Robustly parse boolean debug value from environment."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in (
                "true",
                "1",
                "t",
                "yes",
                "y",
                "debug",
                "dev",
                "development",
            )
        return bool(v)

    @model_validator(mode="after")
    def merge_flat_google_settings(self) -> "Settings":
        """Merge legacy flat GOOGLE_* dotenv values into the typed nested settings."""
        updates: dict[str, str] = {}
        flat_values = {
            "client_id": self.google_client_id,
            "client_secret": self.google_client_secret,
            "client_secrets_file": self.google_client_secrets_file,
            "redirect_uri": self.google_redirect_uri,
            "token_encryption_key": self.google_token_encryption_key,
            "token_encryption_key_file": self.google_token_encryption_key_file,
        }
        for key, value in flat_values.items():
            if value is not None and str(value).strip():
                updates[key] = str(value).strip()
        if updates:
            merged = {
                field_name: getattr(self.google, field_name)
                for field_name in GoogleOAuthSettings.model_fields
            }
            merged.update(updates)
            self.google = GoogleOAuthSettings.model_validate(merged)
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


# Global settings singleton
settings = Settings()
