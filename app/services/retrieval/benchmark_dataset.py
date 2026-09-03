"""Versioned benchmark dataset & corpus for retrieval evaluation (spec P10-21).

Provides curated queries covering all 11 required test categories, along with
expected ground-truth documents, parents, and child hits. Includes a synthetic
multi-document corpus with hierarchical parent-child relationships, tables,
and bilingual content.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.retrieval import ExpansionPolicy, RetrievalMode, RetrievalQuery
from app.domain.models.sufficiency import SufficiencyStatus


class BenchmarkQueryCategory(StrEnum):
    """The 11 mandatory benchmark categories defined in spec P10-21."""

    EXACT_KEYWORD = "exact_keyword"
    SEMANTIC_PARAPHRASE = "semantic_paraphrase"
    MIXED_EN_VI = "mixed_en_vi"
    HEADING_SPECIFIC = "heading_specific"
    TABLE_SPECIFIC = "table_specific"
    DOCUMENT_LOCAL = "document_local"
    CROSS_DOC_COMPARE = "cross_doc_compare"
    NEGATIVE_NO_ANSWER = "negative_no_answer"
    SOURCE_FILTERED = "source_filtered"
    BROAD_WHY_EXPLAIN = "broad_why_explain"
    SPECIFIC_FACT = "specific_fact"


class BenchmarkQuery(BaseModel):
    """A test query with ground-truth labels and evaluation expectations."""

    model_config = ConfigDict(frozen=True)

    query_id: str
    category: BenchmarkQueryCategory
    query_text: str
    description: str = ""
    mode: RetrievalMode = RetrievalMode.CORPUS_SEARCH
    expansion_policy: ExpansionPolicy = ExpansionPolicy.PARENT
    document_ids: list[str] = Field(default_factory=list)
    source_filters: dict[str, Any] = Field(default_factory=dict)

    # Ground truth expectations for evaluation
    expected_doc_ids: list[str] = Field(default_factory=list)
    expected_parent_ids: list[str] = Field(default_factory=list)
    expected_child_ids: list[str] = Field(default_factory=list)
    expected_sufficiency: SufficiencyStatus = SufficiencyStatus.SUFFICIENT

    def to_retrieval_query(self, *, requester_id: str | None = None) -> RetrievalQuery:
        """Convert benchmark query definition into a live RetrievalQuery."""
        return RetrievalQuery(
            original_query=self.query_text,
            search_query=self.query_text,
            mode=self.mode,
            expansion_policy=self.expansion_policy,
            document_ids=self.document_ids,
            source_filters=self.source_filters,
            requester_id=requester_id,
        )


class BenchmarkChunk(BaseModel):
    """A chunk within the benchmark corpus."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    parent_id: str | None = None
    hierarchy_level: int  # 0 for PARENT, 1 for CHILD
    node_type: str = "text"  # "text", "heading", "table", "TABLE_CHILD"
    heading_path: list[str] = Field(default_factory=list)
    chunk_index: int = 0
    page_start: int | None = 1
    page_end: int | None = 1
    content_raw: str
    keywords: list[str] = Field(default_factory=list)


class BenchmarkDocument(BaseModel):
    """A document within the benchmark corpus."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    title: str
    uri: str
    source_type: str = "pdf"
    version_number: int = 1
    description: str = ""


class BenchmarkCorpus(BaseModel):
    """A complete self-contained corpus for deterministic evaluation."""

    model_config = ConfigDict(frozen=True)

    documents: list[BenchmarkDocument]
    chunks: list[BenchmarkChunk]

    def get_document(self, doc_id: str) -> BenchmarkDocument | None:
        for doc in self.documents:
            if doc.document_id == doc_id:
                return doc
        return None

    def get_children(self, doc_id: str | None = None) -> list[BenchmarkChunk]:
        """Return CHILD (level 1) chunks."""
        return [
            c for c in self.chunks
            if c.hierarchy_level == 1 and (doc_id is None or c.document_id == doc_id)
        ]

    def get_parents(self, doc_id: str | None = None) -> list[BenchmarkChunk]:
        """Return PARENT (level 0) chunks."""
        return [
            c for c in self.chunks
            if c.hierarchy_level == 0 and (doc_id is None or c.document_id == doc_id)
        ]


# ---------------------------------------------------------------------------
# Deterministic UUID generator for normative identifiers
# ---------------------------------------------------------------------------

def _bench_uuid(name: str) -> str:
    """Generate deterministic RFC 4122 UUID compliant with sql.py uuid_literal."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"personal_ai_assistant.benchmark.{name}"))


DOC_TECH_ID = _bench_uuid("doc.tech")
DOC_HR_ID = _bench_uuid("doc.hr")
DOC_FIN_ID = _bench_uuid("doc.fin")

P_TECH_1_ID = _bench_uuid("parent.tech.1")
C_TECH_1_1_ID = _bench_uuid("child.tech.1.1")
C_TECH_1_2_ID = _bench_uuid("child.tech.1.2")
P_TECH_2_ID = _bench_uuid("parent.tech.2")
C_TECH_2_1_ID = _bench_uuid("child.tech.2.1")

P_HR_1_ID = _bench_uuid("parent.hr.1")
C_HR_1_1_ID = _bench_uuid("child.hr.1.1")
C_HR_1_2_ID = _bench_uuid("child.hr.1.2")

P_FIN_1_ID = _bench_uuid("parent.fin.1")
C_FIN_1_1_ID = _bench_uuid("child.fin.1.1")
C_FIN_1_2_ID = _bench_uuid("child.fin.1.2")


# ---------------------------------------------------------------------------
# Synthetic Benchmark Corpus (Normative V1 Reference)
# ---------------------------------------------------------------------------

DOC_TECH_SPEC = BenchmarkDocument(
    document_id=DOC_TECH_ID,
    title="Kiến trúc Hệ thống Phân tán V2",
    uri="drive://specs/system_architecture_v2.pdf",
    source_type="pdf",
    version_number=2,
    description="Tài liệu thiết kế kiến trúc microservices và caching",
)

DOC_HR_POLICY = BenchmarkDocument(
    document_id=DOC_HR_ID,
    title="Quy chế Làm việc & Phúc lợi 2026",
    uri="drive://policies/hr_policy_2026.docx",
    source_type="docx",
    version_number=1,
    description="Chính sách nhân sự, ngày nghỉ phép và bảo hiểm",
)

DOC_FINANCIAL_REPORT = BenchmarkDocument(
    document_id=DOC_FIN_ID,
    title="Báo cáo Tài chính Q2 2026",
    uri="drive://finance/q2_2026_financials.pdf",
    source_type="pdf",
    version_number=1,
    description="Bảng doanh thu, chi phí R&D và lợi nhuận các quý",
)

# Chunks for DOC_TECH_SPEC
PARENT_TECH_1 = BenchmarkChunk(
    chunk_id=P_TECH_1_ID,
    document_id=DOC_TECH_ID,
    parent_id=None,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Kiến trúc Microservices"],
    chunk_index=0,
    page_start=1,
    page_end=3,
    content_raw=(
        "Chương 1: Kiến trúc Microservices và Cơ chế Giao tiếp.\n"
        "Hệ thống sử dụng FastAPI làm API Gateway và gRPC cho giao tiếp nội bộ giữa các service. "
        "Redis được triển khai làm distributed cache và message broker cho Celery workers. "
        "Mỗi instance duy trì kết nối pool tối đa 50 connection."
    ),
)

CHILD_TECH_1_1 = BenchmarkChunk(
    chunk_id=C_TECH_1_1_ID,
    document_id=DOC_TECH_ID,
    parent_id=P_TECH_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Kiến trúc Microservices", "API Gateway"],
    chunk_index=1,
    page_start=1,
    page_end=2,
    content_raw=(
        "FastAPI đóng vai trò là API Gateway tiếp nhận toàn bộ HTTP request từ client. "
        "Gateway thực hiện rate limiting bằng thuật toán Token Bucket với Redis backend. "
        "Mỗi user được cấp tối đa 100 request/phút."
    ),
    keywords=["FastAPI", "API Gateway", "rate limiting", "Token Bucket", "Redis"],
)

CHILD_TECH_1_2 = BenchmarkChunk(
    chunk_id=C_TECH_1_2_ID,
    document_id=DOC_TECH_ID,
    parent_id=P_TECH_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Kiến trúc Microservices", "gRPC Inter-service"],
    chunk_index=2,
    page_start=2,
    page_end=3,
    content_raw=(
        "Giao tiếp nội bộ giữa các microservices sử dụng giao thức gRPC với Protobuf serialization. "
        "Connection pool duy trì tối đa 50 connection đồng thời trên mỗi worker pod để tối ưu hóa latency."
    ),
    keywords=["gRPC", "Protobuf", "connection pool", "microservices", "latency"],
)

PARENT_TECH_2 = BenchmarkChunk(
    chunk_id=P_TECH_2_ID,
    document_id=DOC_TECH_ID,
    parent_id=None,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Cơ sở dữ liệu & Vector Index"],
    chunk_index=3,
    page_start=4,
    page_end=5,
    content_raw=(
        "Chương 2: Lưu trữ Dữ liệu và Vector Indexing.\n"
        "PostgreSQL 16 kết hợp pgvector extension được dùng làm cơ sở dữ liệu quan hệ và vector store chính. "
        "Index HNSW được tạo với m=16 và ef_construction=64 cho khoảng cách Cosine distance."
    ),
)

CHILD_TECH_2_1 = BenchmarkChunk(
    chunk_id=C_TECH_2_1_ID,
    document_id=DOC_TECH_ID,
    parent_id=P_TECH_2_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Cơ sở dữ liệu & Vector Index", "HNSW Index"],
    chunk_index=4,
    page_start=4,
    page_end=5,
    content_raw=(
        "Chỉ mục HNSW trên bảng document_chunks sử dụng tham số m=16, ef_construction=64. "
        "Khoảng cách vector được tính bằng vector_cosine_ops, kích thước embedding chuẩn 1536 chiều."
    ),
    keywords=["HNSW", "pgvector", "Cosine", "embedding", "1536", "document_chunks"],
)

# Chunks for DOC_HR_POLICY
PARENT_HR_1 = BenchmarkChunk(
    chunk_id=P_HR_1_ID,
    document_id=DOC_HR_ID,
    parent_id=None,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Chế độ Nghỉ phép"],
    chunk_index=0,
    page_start=1,
    page_end=2,
    content_raw=(
        "Điều 5: Chế độ Nghỉ phép thường niên và Nghỉ ốm đau.\n"
        "Nhân viên chính thức được hưởng 14 ngày phép năm có hưởng nguyên lương. "
        "Mỗi 3 năm thâm niên được cộng thêm 1 ngày phép năm. "
        "Nghỉ ốm đau dưới 3 ngày cần thông báo qua hệ thống trước 8h30 sáng."
    ),
)

CHILD_HR_1_1 = BenchmarkChunk(
    chunk_id=C_HR_1_1_ID,
    document_id=DOC_HR_ID,
    parent_id=P_HR_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Chế độ Nghỉ phép", "Phép năm"],
    chunk_index=1,
    page_start=1,
    page_end=1,
    content_raw=(
        "Mỗi nhân viên chính thức có 14 ngày nghỉ phép hàng năm hưởng lương đầy đủ. "
        "Thời gian thâm niên cứ mỗi 3 năm làm việc liên tục sẽ được cộng thêm 1 ngày phép năm tối đa không quá 20 ngày."
    ),
    keywords=["nghỉ phép", "14 ngày", "phép năm", "thâm niên", "lương"],
)

CHILD_HR_1_2 = BenchmarkChunk(
    chunk_id=C_HR_1_2_ID,
    document_id=DOC_HR_ID,
    parent_id=P_HR_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Chế độ Nghỉ phép", "Nghỉ ốm"],
    chunk_index=2,
    page_start=2,
    page_end=2,
    content_raw=(
        "Nghỉ ốm đau ngắn ngày (1-2 ngày) phải xin phép quản lý trực tiếp qua cổng thông tin nội bộ trước 8:30 sáng. "
        "Nghỉ ốm từ ngày thứ 3 trở đi bắt buộc phải nộp giấy chứng nhận y tế từ bệnh viện."
    ),
    keywords=["nghỉ ốm", "bệnh viện", "giấy chứng nhận", "y tế", "8:30"],
)

# Chunks for DOC_FINANCIAL_REPORT (includes a table)
PARENT_FIN_1 = BenchmarkChunk(
    chunk_id=P_FIN_1_ID,
    document_id=DOC_FIN_ID,
    parent_id=None,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Kết quả Tài chính Q2 2026"],
    chunk_index=0,
    page_start=2,
    page_end=4,
    content_raw=(
        "Mục 3: Chi tiết Doanh thu và Chi phí Quý 2 năm 2026.\n"
        "Tổng doanh thu toàn công ty đạt 45.2 tỷ VNĐ, tăng trưởng 18% so với cùng kỳ năm 2025. "
        "Chi phí đầu tư nghiên cứu R&D chiếm 12.5 tỷ VNĐ, trong đó tập trung vào hạ tầng AI và Cloud."
    ),
)

CHILD_FIN_1_1 = BenchmarkChunk(
    chunk_id=C_FIN_1_1_ID,
    document_id=DOC_FIN_ID,
    parent_id=P_FIN_1_ID,
    hierarchy_level=1,
    node_type="table",
    heading_path=["Kết quả Tài chính Q2 2026", "Bảng Phân bổ Chi phí"],
    chunk_index=1,
    page_start=3,
    page_end=3,
    content_raw=(
        "| Hạng mục | Chi phí Q1 (Tỷ VNĐ) | Chi phí Q2 (Tỷ VNĐ) | Tăng trưởng |\n"
        "|---|---|---|---|\n"
        "| Nghiên cứu AI & R&D | 10.0 | 12.5 | +25% |\n"
        "| Hạ tầng Máy chủ Cloud | 6.2 | 7.8 | +25.8% |\n"
        "| Chi phí Nhân sự | 15.1 | 16.0 | +5.9% |\n"
        "| Marketing & Bán hàng | 8.5 | 8.9 | +4.7% |"
    ),
    keywords=["Bảng Phân bổ Chi phí", "Nghiên cứu AI", "R&D", "Hạ tầng Máy chủ Cloud", "12.5", "7.8"],
)

CHILD_FIN_1_2 = BenchmarkChunk(
    chunk_id=C_FIN_1_2_ID,
    document_id=DOC_FIN_ID,
    parent_id=P_FIN_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Kết quả Tài chính Q2 2026", "Lợi nhuận ròng"],
    chunk_index=2,
    page_start=4,
    page_end=4,
    content_raw=(
        "Lợi nhuận ròng sau thuế Q2 đạt 8.6 tỷ VNĐ, đạt 108% kế hoạch quý đã đề ra. "
        "Dòng tiền thuần từ hoạt động kinh doanh duy trì dương liên tục 4 quý."
    ),
    keywords=["Lợi nhuận ròng", "8.6 tỷ", "sau thuế", "kế hoạch"],
)

BENCHMARK_CORPUS = BenchmarkCorpus(
    documents=[DOC_TECH_SPEC, DOC_HR_POLICY, DOC_FINANCIAL_REPORT],
    chunks=[
        PARENT_TECH_1, CHILD_TECH_1_1, CHILD_TECH_1_2,
        PARENT_TECH_2, CHILD_TECH_2_1,
        PARENT_HR_1, CHILD_HR_1_1, CHILD_HR_1_2,
        PARENT_FIN_1, CHILD_FIN_1_1, CHILD_FIN_1_2,
    ],
)


# ---------------------------------------------------------------------------
# Standard Curated Benchmark Queries (All 11 Categories)
# ---------------------------------------------------------------------------

BENCHMARK_QUERIES: list[BenchmarkQuery] = [
    # 1. EXACT_KEYWORD
    BenchmarkQuery(
        query_id="q01_exact_kw",
        category=BenchmarkQueryCategory.EXACT_KEYWORD,
        query_text="FastAPI Token Bucket",
        description="Truy vấn từ khóa kỹ thuật chính xác về rate limiting của API gateway",
        expected_doc_ids=[DOC_TECH_ID],
        expected_parent_ids=[P_TECH_1_ID],
        expected_child_ids=[C_TECH_1_1_ID],
    ),
    # 2. SEMANTIC_PARAPHRASE
    BenchmarkQuery(
        query_id="q02_semantic_paraphrase",
        category=BenchmarkQueryCategory.SEMANTIC_PARAPHRASE,
        query_text="Chính sách số ngày được nghỉ trong năm của cán bộ nhân viên công ty",
        description="Diễn đạt tự nhiên chính sách phép năm không dùng từ 'thường niên'",
        expected_doc_ids=[DOC_HR_ID],
        expected_parent_ids=[P_HR_1_ID],
        expected_child_ids=[C_HR_1_1_ID],
    ),
    # 3. MIXED_EN_VI
    BenchmarkQuery(
        query_id="q03_mixed_en_vi",
        category=BenchmarkQueryCategory.MIXED_EN_VI,
        query_text="Cấu hình connection pool gRPC và latency giữa các service",
        description="Truy vấn hỗn hợp thuật ngữ tiếng Anh và tiếng Việt",
        expected_doc_ids=[DOC_TECH_ID],
        expected_parent_ids=[P_TECH_1_ID],
        expected_child_ids=[C_TECH_1_2_ID],
    ),
    # 4. HEADING_SPECIFIC
    BenchmarkQuery(
        query_id="q04_heading_specific",
        category=BenchmarkQueryCategory.HEADING_SPECIFIC,
        query_text="Cơ sở dữ liệu & Vector Index HNSW Index",
        description="Tìm kiếm theo tiêu đề phân đoạn cụ thể trong tài liệu kỹ thuật",
        expected_doc_ids=[DOC_TECH_ID],
        expected_parent_ids=[P_TECH_2_ID],
        expected_child_ids=[C_TECH_2_1_ID],
    ),
    # 5. TABLE_SPECIFIC
    BenchmarkQuery(
        query_id="q05_table_specific",
        category=BenchmarkQueryCategory.TABLE_SPECIFIC,
        query_text="Chi phí cho Nghiên cứu AI và Hạ tầng Máy chủ Cloud trong Quý 2 là bao nhiêu?",
        description="Truy vấn thông tin nằm trong bảng số liệu phân bổ chi phí",
        expected_doc_ids=[DOC_FIN_ID],
        expected_parent_ids=[P_FIN_1_ID],
        expected_child_ids=[C_FIN_1_1_ID],
    ),
    # 6. DOCUMENT_LOCAL
    BenchmarkQuery(
        query_id="q06_doc_local",
        category=BenchmarkQueryCategory.DOCUMENT_LOCAL,
        query_text="Quy định khi bị ốm phải báo trước mấy giờ và giấy chứng nhận viện",
        description="Truy vấn giới hạn phạm vi trong một văn bản chính sách",
        mode=RetrievalMode.DOCUMENT_SEARCH,
        document_ids=[DOC_HR_ID],
        expected_doc_ids=[DOC_HR_ID],
        expected_parent_ids=[P_HR_1_ID],
        expected_child_ids=[C_HR_1_2_ID],
    ),
    # 7. CROSS_DOC_COMPARE
    BenchmarkQuery(
        query_id="q07_cross_compare",
        category=BenchmarkQueryCategory.CROSS_DOC_COMPARE,
        query_text="So sánh định hướng đầu tư công nghệ trong kiến trúc và ngân sách tài chính Q2",
        description="So sánh chéo thông tin giữa tài liệu kiến trúc kỹ thuật và báo cáo tài chính",
        mode=RetrievalMode.COMPARE_DOCUMENTS,
        document_ids=[DOC_TECH_ID, DOC_FIN_ID],
        expected_doc_ids=[DOC_TECH_ID, DOC_FIN_ID],
        expected_parent_ids=[P_TECH_2_ID, P_FIN_1_ID],
        expected_child_ids=[C_TECH_2_1_ID, C_FIN_1_1_ID],
    ),
    # 8. NEGATIVE_NO_ANSWER
    BenchmarkQuery(
        query_id="q08_negative_no_ans",
        category=BenchmarkQueryCategory.NEGATIVE_NO_ANSWER,
        query_text="Chính sách cấp xe ô tô và công tác phí nước ngoài cho giám đốc kinh doanh",
        description="Truy vấn nội dung hoàn toàn không tồn tại trong kho tài liệu nội bộ",
        expected_doc_ids=[],
        expected_parent_ids=[],
        expected_child_ids=[],
        expected_sufficiency=SufficiencyStatus.INSUFFICIENT,
    ),
    # 9. SOURCE_FILTERED
    BenchmarkQuery(
        query_id="q09_source_filtered",
        category=BenchmarkQueryCategory.SOURCE_FILTERED,
        query_text="Quy định nghỉ phép và thâm niên làm việc",
        description="Lọc nguồn theo định dạng file docx của phòng nhân sự",
        source_filters={"source_type": "docx"},
        expected_doc_ids=[DOC_HR_ID],
        expected_parent_ids=[P_HR_1_ID],
        expected_child_ids=[C_HR_1_1_ID],
    ),
    # 10. BROAD_WHY_EXPLAIN
    BenchmarkQuery(
        query_id="q10_broad_why_explain",
        category=BenchmarkQueryCategory.BROAD_WHY_EXPLAIN,
        query_text="Giải thích tại sao hệ thống lại sử dụng PostgreSQL pgvector và index HNSW",
        description="Câu hỏi tổng quan giải thích lý do thiết kế kiến trúc",
        expected_doc_ids=[DOC_TECH_ID],
        expected_parent_ids=[P_TECH_2_ID],
        expected_child_ids=[C_TECH_2_1_ID],
    ),
    # 11. SPECIFIC_FACT
    BenchmarkQuery(
        query_id="q11_specific_fact",
        category=BenchmarkQueryCategory.SPECIFIC_FACT,
        query_text="Lợi nhuận ròng sau thuế đạt bao nhiêu tỷ trong Q2 năm 2026?",
        description="Truy vấn số liệu sự thật chính xác tuyệt đối",
        expected_doc_ids=[DOC_FIN_ID],
        expected_parent_ids=[P_FIN_1_ID],
        expected_child_ids=[C_FIN_1_2_ID],
    ),
]
