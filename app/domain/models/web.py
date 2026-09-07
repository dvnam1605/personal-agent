"""Web search domain contracts (spec P13).

Minimal frozen models for external web results consumed by the
KnowledgeResearchAgent. Web content is untrusted external data: it is always
labeled with its source URL and kept separate from internal evidence.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebSearchResultItem(BaseModel):
    """One external web result with mandatory source provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(
        ...,
        min_length=1,
        description="Canonical source URL of the external result.",
    )
    title: str = Field(
        default="",
        description="Result title as returned by the provider.",
    )
    snippet: str = Field(
        default="",
        description="Provider-generated text excerpt (untrusted).",
    )
    source: str = Field(
        default="web",
        description="Provider or vertical label (e.g. 'web', 'news').",
    )

    @field_validator("title", "snippet", "source", mode="after")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """Normalize surrounding whitespace in provider text."""
        return value.strip()

    @field_validator("url", mode="after")
    @classmethod
    def reject_blank_url(cls, value: str) -> str:
        """Enforce mandatory source provenance (min_length runs pre-strip)."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Web search result URL cannot be blank.")
        return normalized


class WebSearchPage(BaseModel):
    """One page of external web results (spec P13 §3.1 Mode 2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[WebSearchResultItem] = Field(default_factory=list)
    query: str = Field(
        default="",
        description="Normalized provider query that produced this page.",
    )


__all__ = ["WebSearchPage", "WebSearchResultItem"]
