"""Document pipeline settings: embedding, reranking, parsing, profiling and chunking."""

from pydantic import BaseModel, Field, model_validator


class EmbeddingSettings(BaseModel):
    model: str = Field(
        default="AITeamVN/Vietnamese_Embedding",
        description="Embedding model name (local snapshot by default, ADR 0012)",
    )
    revision: str = Field(
        default="dea33aa1ab339f38d66ae0a40e6c40e0a9249568",
        description="Hub commit the model is pinned to, so a changed upstream repo is never pulled",
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
    device: str | None = Field(
        default=None,
        description=(
            "Torch device for local embedding inference: 'cuda', 'cpu', or null "
            "to auto-select (cuda when available)."
        ),
    )


class RerankerSettings(BaseModel):
    model: str = Field(
        default="namdp-ptit/ViRanker",
        description="Cross-encoder reranker model (local snapshot by default, ADR 0012)",
    )
    revision: str = Field(
        default="922bd0d698b12e02f37717051374f40a5297e6a9",
        description="Hub commit the model is pinned to, so a changed upstream repo is never pulled",
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
    allow_network_download: bool = Field(
        default=False,
        description=(
            "Air-gapped safety: when False (default), the reranker never contacts the HuggingFace "
            "hub at runtime if the local snapshot is missing, failing loud instead of hanging."
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
    pdf_engine: str = Field(
        default="auto",
        pattern="^(auto|docling|pymupdf)$",
        description=(
            "PDF parser: 'docling' always; 'pymupdf' for every text-layer PDF; 'auto' "
            "PyMuPDF for long text-layer PDFs (Docling needs GBs of RAM on books), "
            "Docling otherwise. Scanned PDFs always go to Docling (NEEDS_OCR)."
        ),
    )
    pymupdf_min_pages: int = Field(
        default=20,
        ge=1,
        description="In 'auto' mode, text-layer PDFs with at least this many pages use PyMuPDF",
    )


class ProfilingSettings(BaseModel):
    """Document category + metadata profiling at ingest (rules first, LLM if unsure)."""

    llm_enabled: bool = Field(
        default=True,
        description="Allow one LLM extraction call when rule confidence is low",
    )
    llm_threshold: float = Field(
        default=0.6,
        description="Category confidence below which the LLM fallback runs",
    )
    llm_timeout_seconds: float = Field(
        default=30.0,
        description="Wall-clock cap for the profiling LLM call",
    )


class ChunkingSettings(BaseModel):
    """Parent/child chunk budgets and merging policy (spec P9C)."""

    parent_target_tokens: int = Field(
        default=1600,
        ge=1,
        description="Target size for PARENT chunks (benchmark start ~1200-2000)",
    )
    parent_hard_max_tokens: int = Field(
        default=2400,
        ge=1,
        description="Hard maximum for PARENT chunks before the split ladder kicks in",
    )
    child_target_tokens: int = Field(
        default=500,
        ge=1,
        description="Target size for CHILD chunks (benchmark start ~350-650)",
    )
    child_hard_max_tokens: int = Field(
        default=800,
        ge=1,
        description="Hard maximum for CHILD chunks; never exceeded by construction",
    )
    merge_small_nodes_below_tokens: int = Field(
        default=40,
        ge=0,
        description="Adjacent tiny paragraphs/lists may merge within one parent",
    )

    @model_validator(mode="after")
    def validate_hard_bounds(self) -> "ChunkingSettings":
        """Reject hard maxima below their corresponding targets."""
        if self.child_hard_max_tokens < self.child_target_tokens:
            raise ValueError(
                "child_hard_max_tokens must be >= child_target_tokens "
                f"({self.child_hard_max_tokens} < {self.child_target_tokens})."
            )
        if self.parent_hard_max_tokens < self.parent_target_tokens:
            raise ValueError(
                "parent_hard_max_tokens must be >= parent_target_tokens "
                f"({self.parent_hard_max_tokens} < {self.parent_target_tokens})."
            )
        if self.merge_small_nodes_below_tokens > self.child_hard_max_tokens:
            raise ValueError(
                "merge_small_nodes_below_tokens must be <= child_hard_max_tokens "
                f"({self.merge_small_nodes_below_tokens} > {self.child_hard_max_tokens})."
            )
        if self.merge_small_nodes_below_tokens > self.parent_hard_max_tokens:
            raise ValueError(
                "merge_small_nodes_below_tokens must be <= parent_hard_max_tokens "
                f"({self.merge_small_nodes_below_tokens} > {self.parent_hard_max_tokens})."
            )
        return self
