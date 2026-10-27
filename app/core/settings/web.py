"""Web search settings (Tavily)."""

from pydantic import BaseModel, Field


class WebSearchSettings(BaseModel):
    """External web search used only when the user asks for it explicitly.

    Without ``api_key`` the feature reports itself unavailable instead of failing.
    """

    api_key: str | None = Field(default=None, repr=False, description="Tavily API key")
    base_url: str = Field(default="https://api.tavily.com", description="Tavily API base URL")
    max_results: int = Field(default=5, ge=1, le=10, description="Results read per question")
    search_depth: str = Field(default="basic", description="Tavily search depth: basic | advanced")
    timeout_seconds: float = Field(default=12.0, gt=0.0, description="Provider request timeout")
