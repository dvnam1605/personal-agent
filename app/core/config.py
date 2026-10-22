"""Typed application settings using Pydantic Settings.

``Settings`` composes the sub-settings groups defined in ``app.core.settings``;
every group is re-exported here so callers keep importing from this module.
"""

from enum import StrEnum

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings.budgets import (
    LLMBudgetSettings,
    ReActBudgetSettings,
    RouteBudget,
    SupervisorBudgetSettings,
    TimeoutsSettings,
)
from app.core.settings.google import GoogleOAuthSettings
from app.core.settings.infrastructure import (
    DatabaseSettings,
    LangSmithSettings,
    RedisSettings,
    SecuritySettings,
)
from app.core.settings.knowledge import (
    ChunkingSettings,
    EmbeddingSettings,
    ParsingSettings,
    ProfilingSettings,
    RerankerSettings,
)
from app.core.settings.llm import LLMMode, LLMProviderSettings
from app.core.settings.paths import PROJECT_ROOT, resolve_project_path

__all__ = [
    "PROJECT_ROOT",
    "ChunkingSettings",
    "DatabaseSettings",
    "EmbeddingSettings",
    "Environment",
    "GoogleOAuthSettings",
    "LLMBudgetSettings",
    "LLMMode",
    "LLMProviderSettings",
    "LangSmithSettings",
    "ParsingSettings",
    "ProfilingSettings",
    "ReActBudgetSettings",
    "RedisSettings",
    "RerankerSettings",
    "RouteBudget",
    "SecuritySettings",
    "Settings",
    "SupervisorBudgetSettings",
    "TimeoutsSettings",
    "get_settings",
    "resolve_project_path",
    "settings",
]


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Main Application Settings."""

    app_name: str = "Personal AI Assistant"
    app_version: str = "0.1.0"
    environment: Environment = Environment.DEVELOPMENT
    deploy_target: str | None = Field(
        default=None,
        description=(
            "Independent deployment target: local | staging | production. "
            "If staging/production, ENVIRONMENT must not be development/testing (H5)."
        ),
    )
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
    # Backward-compatible flat LLM env vars (M6)
    openai_api_key: str | None = Field(default=None, repr=False, exclude=True)
    anthropic_api_key: str | None = Field(default=None, repr=False, exclude=True)
    gemini_api_key: str | None = Field(default=None, repr=False, exclude=True)
    llm_mode: str | None = Field(default=None, repr=False, exclude=True)
    kira_api_key: str | None = Field(default=None, repr=False, exclude=True)
    kira_model: str | None = Field(default=None, repr=False, exclude=True)
    kira_base_url: str | None = Field(default=None, repr=False, exclude=True)
    router_api_key: str | None = Field(default=None, repr=False, exclude=True)
    router_model: str | None = Field(default=None, repr=False, exclude=True)
    router_base_url: str | None = Field(default=None, repr=False, exclude=True)
    langsmith: LangSmithSettings = Field(default_factory=LangSmithSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    parsing: ParsingSettings = Field(default_factory=ParsingSettings)
    profiling: ProfilingSettings = Field(default_factory=ProfilingSettings)
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

    @property
    def docs_enabled(self) -> bool:
        """API docs (/docs, /openapi.json) are enabled in development/testing, or when debug is True (M10)."""
        if self.environment in (Environment.DEVELOPMENT, Environment.TESTING):
            return True
        return bool(self.debug)

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
        """Merge legacy flat GOOGLE_* and flat LLM dotenv values into the typed nested settings."""
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

        # Merge flat LLM settings (M6) plus Kira / local-router modes
        llm_updates: dict[str, str] = {}
        openai_key = (self.openai_api_key or "").strip()
        anthropic_key = (self.anthropic_api_key or "").strip()
        gemini_key = (self.gemini_api_key or "").strip()
        kira_key = (self.kira_api_key or "").strip()
        kira_model = (self.kira_model or "").strip()
        kira_base = (self.kira_base_url or "").strip()
        router_key = (self.router_api_key or "").strip()
        router_model = (self.router_model or "").strip()
        router_base = (self.router_base_url or "").strip()
        mode = (self.llm_mode or "").strip().lower()
        if openai_key and not self.llm.openai_api_key:
            llm_updates["openai_api_key"] = openai_key
        if anthropic_key and not self.llm.anthropic_api_key:
            llm_updates["anthropic_api_key"] = anthropic_key
        if gemini_key and not self.llm.gemini_api_key:
            llm_updates["gemini_api_key"] = gemini_key
        if kira_key and not self.llm.kira_api_key:
            llm_updates["kira_api_key"] = kira_key
        if kira_model:
            llm_updates["kira_model"] = kira_model
        if kira_base:
            llm_updates["kira_base_url"] = kira_base
        if router_key and not self.llm.router_api_key:
            llm_updates["router_api_key"] = router_key
        if router_model:
            llm_updates["router_model"] = router_model
        if router_base:
            llm_updates["router_base_url"] = router_base
        if mode in {LLMMode.OPENAI, LLMMode.KIRA, LLMMode.ROUTER}:
            llm_updates["mode"] = mode
        if llm_updates:
            merged_llm = {
                field_name: getattr(self.llm, field_name)
                for field_name in LLMProviderSettings.model_fields
            }
            merged_llm.update(llm_updates)
            self.llm = LLMProviderSettings.model_validate(merged_llm)
        return self

    @model_validator(mode="after")
    def validate_deploy_target_and_local_urls(self) -> "Settings":
        """Refuse fail-open: remote URLs cannot run as development; deploy_target pins env (H5)."""
        target = (self.deploy_target or "").strip().lower() or None
        if target is not None and target not in {"local", "staging", "production"}:
            raise ValueError("DEPLOY_TARGET must be one of: local, staging, production.")
        if target in {"staging", "production"} and self.environment in (
            Environment.DEVELOPMENT,
            Environment.TESTING,
        ):
            raise ValueError(
                f"DEPLOY_TARGET={target} is incompatible with ENVIRONMENT={self.environment.value}. "
                "Set ENVIRONMENT to staging or production."
            )
        if self.environment in (Environment.DEVELOPMENT, Environment.TESTING):
            db_url = self.database.url.lower()
            local_markers = (
                "localhost",
                "127.0.0.1",
                ":5434",
                "sqlite",
                ":memory:",
                "@postgres:",  # docker compose service DNS
                "@postgres",
                "assistant_postgres",
                "host.docker.internal",
            )
            if not any(marker in db_url for marker in local_markers):
                raise ValueError(
                    f"ENVIRONMENT={self.environment.value} but DATABASE__URL is not local. "
                    "Refusing to run with production-like data stores while security is relaxed. "
                    "Set ENVIRONMENT=production or point DATABASE__URL at localhost."
                )
        if self.environment in (Environment.PRODUCTION, Environment.STAGING):
            bound = (self.security.api_key_user_id or "").strip()
            if self.security.api_key and not bound:
                raise ValueError(
                    "SECURITY__API_KEY_USER_ID must be set in staging/production so the "
                    "shared API key cannot impersonate arbitrary X-User-ID values (H3)."
                )
        return self

    @model_validator(mode="after")
    def validate_production_credentials(self) -> "Settings":
        """Reject default development database/redis credentials in production/staging (H6)."""
        if self.environment in (Environment.PRODUCTION, Environment.STAGING):
            db_url = self.database.url.lower()
            if "postgres:postgres@" in db_url or "localhost:5434" in db_url:
                raise ValueError(
                    f"Default database credentials/host ('postgres:postgres@...:5434') "
                    f"are forbidden in {self.environment.value}. Set a secure DATABASE__URL."
                )
            from urllib.parse import urlparse

            redis = urlparse(self.redis.url)
            if not redis.password:
                raise ValueError(
                    f"Redis URL must include a password in {self.environment.value}. "
                    "Set REDIS__URL with credentials."
                )
        return self

    @model_validator(mode="after")
    def validate_approval_signing_key(self) -> "Settings":
        """Eager HMAC key check so a missing key fails at process start, not first token."""
        key = self.security.approval_signing_key
        raw = key.encode("utf-8") if key else b""
        if self.environment not in (Environment.DEVELOPMENT, Environment.TESTING):
            if len(raw) < 32:
                raise ValueError(
                    "SECURITY__APPROVAL_SIGNING_KEY must be set to at least 32 bytes "
                    "in staging/production. Forgetting this env lets tokens be forged."
                )
            return self
        if len(raw) < 32:
            import logging

            logging.getLogger(__name__).warning(
                "approval_signing_key_using_test_default",
                extra={"environment": self.environment.value},
            )
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


# Global settings singleton
settings = Settings()


def get_settings() -> Settings:
    """Return the global Settings singleton instance."""
    return settings
