"""LLM provider selection (OpenAI, Kira, local router) and its credentials."""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class LLMMode(StrEnum):
    OPENAI = "openai"
    KIRA = "kira"
    ROUTER = "router"


class LLMProviderSettings(BaseModel):
    mode: LLMMode = Field(default=LLMMode.OPENAI, description="Active OpenAI-compatible backend")
    openai_api_key: str | None = Field(default=None, description="OpenAI API Key")
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API Key")
    gemini_api_key: str | None = Field(default=None, description="Google Gemini API Key")
    primary_model: str = Field(default="gpt-4o-mini", description="Primary reasoning model")
    fast_model: str = Field(default="gpt-4o-mini", description="Fast classifier model")
    temperature: float = Field(default=0.0, description="Default sampling temperature")
    base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible chat base URL (no trailing /chat/completions)",
    )
    kira_api_key: str | None = Field(default=None, description="Kira gateway API key")
    kira_model: str = Field(default="glm-5.3-flash-free", description="Kira model id")
    kira_base_url: str | None = Field(
        default="https://kiraai.vn/api/v1",
        description="Kira OpenAI-compatible base URL",
    )
    router_api_key: str | None = Field(default=None, description="Local router API key")
    router_model: str = Field(default="cu/default", description="Local router model id")
    router_base_url: str = Field(
        default="http://localhost:20128/v1",
        description="Local OpenAI-compatible router base URL",
    )

    @field_validator(
        "openai_api_key",
        "kira_api_key",
        "kira_base_url",
        "router_api_key",
        "base_url",
        mode="before",
    )
    @classmethod
    def empty_strings_are_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def apply_selected_mode(self) -> "LLMProviderSettings":
        """Copy the active mode's key/model/base_url onto the fields call sites already read."""
        if self.mode == LLMMode.OPENAI:
            # Native OpenAI: keep base_url None (or custom if explicitly configured)
            return self
        if self.mode == LLMMode.ROUTER:
            key = (self.router_api_key or "").strip() or None
            model = (self.router_model or "").strip() or "cu/default"
            base = (self.router_base_url or "").strip() or "http://localhost:20128/v1"
            self.openai_api_key = key
            self.primary_model = model
            self.fast_model = model
            self.base_url = base
            return self
        if self.mode == LLMMode.KIRA:
            kira_key = (self.kira_api_key or "").strip() or None
            if kira_key:
                self.openai_api_key = kira_key
                model = (self.kira_model or "").strip() or "glm-5.3-flash-free"
                self.primary_model = model
                self.fast_model = model
                kira_base = (self.kira_base_url or "").strip() or "https://kiraai.vn/api/v1"
                self.base_url = kira_base
            return self
        return self

    def chat_completions_url(self) -> str:
        """POST target for OpenAI-compatible chat completions."""
        base = (self.base_url or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/chat/completions"
