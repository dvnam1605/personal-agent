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

    def get_chunk(self, chunk_id: str) -> BenchmarkChunk | None:
        """Find a chunk by its chunk_id."""
        for c in self.chunks:
            if c.chunk_id == chunk_id:
                return c
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


# ---------------------------------------------------------------------------
# Real Document UUIDs (from data/QuyetDinh: DuToan 427, NhanSu 80, QuyChe 587)
# ---------------------------------------------------------------------------

DOC_DUTOAN_ID = _bench_uuid("doc.dutoan.427")
DOC_NHANSU_ID = _bench_uuid("doc.nhansu.80")
DOC_QUYCHE_ID = _bench_uuid("doc.quyche.587")

# Aliases for backwards compatibility
DOC_FIN_ID = DOC_DUTOAN_ID
DOC_HR_ID = DOC_NHANSU_ID
DOC_TECH_ID = DOC_QUYCHE_ID

# Quyết định 427/QĐ-TNVN (Dự toán Hoạt động thông tin khoa học 2026)
P_DUTOAN_1_ID = _bench_uuid("parent.dutoan.1")
C_DUTOAN_1_1_ID = _bench_uuid("child.dutoan.1.1")
C_DUTOAN_1_2_ID = _bench_uuid("child.dutoan.1.2")

P_DUTOAN_2_ID = _bench_uuid("parent.dutoan.2")
C_DUTOAN_2_1_ID = _bench_uuid("child.dutoan.2.1")
C_DUTOAN_2_2_ID = _bench_uuid("child.dutoan.2.2")

# Quyết định 80-QĐ/TNVN (Chấm dứt HĐ làm việc bà Cao Thị Hoa Hương)
P_NHANSU_1_ID = _bench_uuid("parent.nhansu.1")
C_NHANSU_1_1_ID = _bench_uuid("child.nhansu.1.1")
C_NHANSU_1_2_ID = _bench_uuid("child.nhansu.1.2")

# Quyết định 587/QĐ-TNVN (Quy chế chấm điểm Liên hoan Phát thanh 2026)
P_QUYCHE_1_ID = _bench_uuid("parent.quyche.1")
C_QUYCHE_1_1_ID = _bench_uuid("child.quyche.1.1")
C_QUYCHE_1_2_ID = _bench_uuid("child.quyche.1.2")

P_QUYCHE_2_ID = _bench_uuid("parent.quyche.2")
C_QUYCHE_2_1_ID = _bench_uuid("child.quyche.2.1")

# Aliases for legacy symbol support
P_FIN_1_ID = P_DUTOAN_1_ID
C_FIN_1_1_ID = C_DUTOAN_2_1_ID
C_FIN_1_2_ID = C_DUTOAN_1_1_ID

P_HR_1_ID = P_NHANSU_1_ID
C_HR_1_1_ID = C_NHANSU_1_1_ID
C_HR_1_2_ID = C_NHANSU_1_2_ID

P_TECH_1_ID = P_QUYCHE_1_ID
C_TECH_1_1_ID = C_QUYCHE_1_1_ID
C_TECH_1_2_ID = C_QUYCHE_1_2_ID
P_TECH_2_ID = P_QUYCHE_2_ID
C_TECH_2_1_ID = C_QUYCHE_2_1_ID


# ---------------------------------------------------------------------------
# Real Document Benchmark Corpus (data/QuyetDinh)
# ---------------------------------------------------------------------------

DOC_DUTOAN_427 = BenchmarkDocument(
    document_id=DOC_DUTOAN_ID,
    title="Quyết định 427/QĐ-TNVN phê duyệt dự toán Hoạt động thông tin khoa học năm 2026",
    uri="file:///d:/Code/personal_ai_assistant/data/QuyetDinh/DuToan/18-3-2026-954776_427QD_25_02_2026.md",
    source_type="md",
    version_number=1,
    description="Phê duyệt dự toán 500 triệu đồng cho Trung tâm R&D và bảng chi phí phần mềm AI, hội thảo",
)

DOC_NHANSU_80 = BenchmarkDocument(
    document_id=DOC_NHANSU_ID,
    title="Quyết định 80-QĐ/TNVN về chấm dứt hợp đồng làm việc đối với viên chức",
    uri="file:///d:/Code/personal_ai_assistant/data/QuyetDinh/NhanSu.TienLuong/14-5-2026-1125655_80QD_28_04_2026.md",
    source_type="md",
    version_number=1,
    description="Chấm dứt hợp đồng làm việc đối với bà Cao Thị Hoa Hương thuộc Trung tâm Kỹ thuật",
)

DOC_QUYCHE_587 = BenchmarkDocument(
    document_id=DOC_QUYCHE_ID,
    title="Quyết định 587/QĐ-TNVN Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII",
    uri="file:///d:/Code/personal_ai_assistant/data/QuyetDinh/QuyChe.QuyDinh/19-3-2026-158402_587QD_16_03_2026.md",
    source_type="md",
    version_number=1,
    description="Ban hành Quy chế chấm điểm tác phẩm tham dự Liên hoan Phát thanh 2026 tại Quảng Ninh",
)

# Aliases
DOC_FINANCIAL_REPORT = DOC_DUTOAN_427
DOC_HR_POLICY = DOC_NHANSU_80
DOC_TECH_SPEC = DOC_QUYCHE_587

# Chunks for DOC_DUTOAN_427 (Dự toán 500 triệu và bảng phần mềm AI)
PARENT_DUTOAN_1 = BenchmarkChunk(
    chunk_id=P_DUTOAN_1_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=None,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định phê duyệt dự toán"],
    chunk_index=0,
    page_start=1,
    page_end=1,
    content_raw=(
        "QUYẾT ĐỊNH Về việc phê duyệt dự toán Hoạt động thông tin khoa học Đài Tiếng nói Việt Nam năm 2026.\n"
        "Điều 1. Phê duyệt dự toán kinh phí đối với Hoạt động thông tin khoa học Đài TNVN năm 2026 (Loại 100, khoản 103) "
        "do Trung tâm Nghiên cứu và ứng dụng Công nghệ Truyền thông (R&D) tổ chức thực hiện với tổng số tiền là 500.000.000 đồng "
        "(Năm trăm triệu đồng chẵn) chi tiết theo Phụ lục đính kèm.\n"
        "Nguồn kinh phí thực hiện: Dự toán kinh phí NSNN được Đài TNVN giao cho đơn vị năm 2026.\n"
        "Điều 2. Trung tâm R&D chịu trách nhiệm quản lý, sử dụng, thanh quyết toán nguồn kinh phí Hoạt động thông tin khoa học Đài TNVN theo quy định hiện hành.\n"
        "Điều 3. Giám đốc Trung tâm R&D, Trưởng ban Ban Kế hoạch - Tài chính và Thủ trưởng các đơn vị có liên quan chịu trách nhiệm thi hành Quyết định này."
    ),
)

CHILD_DUTOAN_1_1 = BenchmarkChunk(
    chunk_id=C_DUTOAN_1_1_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=P_DUTOAN_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định phê duyệt dự toán", "Điều 1: Mức kinh phí"],
    chunk_index=1,
    page_start=1,
    page_end=1,
    content_raw=(
        "Điều 1. Phê duyệt dự toán kinh phí đối với Hoạt động thông tin khoa học Đài TNVN năm 2026 (Loại 100, khoản 103) "
        "do Trung tâm Nghiên cứu và ứng dụng Công nghệ Truyền thông (R&D) tổ chức thực hiện với tổng số tiền là 500.000.000 đồng "
        "(Năm trăm triệu đồng chẵn) chi tiết theo Phụ lục đính kèm. Nguồn kinh phí thực hiện: Dự toán kinh phí NSNN được Đài TNVN giao cho đơn vị năm 2026."
    ),
    keywords=["427/QĐ-TNVN", "dự toán", "kinh phí", "thông tin khoa học", "500.000.000", "500 triệu", "Trung tâm R&D", "Điều 1"],
)

CHILD_DUTOAN_1_2 = BenchmarkChunk(
    chunk_id=C_DUTOAN_1_2_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=P_DUTOAN_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định phê duyệt dự toán", "Điều 2 & Điều 3: Trách nhiệm thi hành"],
    chunk_index=2,
    page_start=1,
    page_end=1,
    content_raw=(
        "Điều 2. Trung tâm R&D chịu trách nhiệm quản lý, sử dụng, thanh quyết toán nguồn kinh phí Hoạt động thông tin khoa học Đài TNVN theo quy định hiện hành.\n"
        "Điều 3. Giám đốc Trung tâm R&D, Trưởng ban Ban Kế hoạch - Tài chính và Thủ trưởng các đơn vị có liên quan chịu trách nhiệm thi hành Quyết định này."
    ),
    keywords=["Điều 2", "Điều 3", "Trung tâm R&D", "Ban Kế hoạch - Tài chính", "KHTC", "thanh quyết toán", "thi hành"],
)

PARENT_DUTOAN_2 = BenchmarkChunk(
    chunk_id=P_DUTOAN_2_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=None,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Phụ lục Dự toán kinh phí năm 2026"],
    chunk_index=3,
    page_start=2,
    page_end=3,
    content_raw=(
        "PHỤ LỤC DỰ TOÁN KINH PHÍ NĂM 2026 - Hoạt động thông tin khoa học của Đài Tiếng nói Việt Nam "
        "(Kèm theo Quyết định số 427/QĐ-TNVN ngày 25 tháng 02 năm 2026 của Đài Tiếng nói Việt Nam). "
        "Chi tiết gồm: Mục I Phần mềm (Plagiarism Checker X, Second Copy, Google Workspace Notebooklm AI, Thư viện pháp luật, ChatGPT Business) "
        "tổng 84.600.000 đồng; Mục II Hội nghị hội thảo 98.100.000 đồng; Mục III Hội đồng tư vấn 152.400.000 đồng; "
        "Mục IV Tuyên truyền trên sóng 90.000.000 đồng; Mục V Tuyên truyền Báo điện tử 74.900.000 đồng. Tổng cộng 500.000.000 đồng."
    ),
)

CHILD_DUTOAN_2_1 = BenchmarkChunk(
    chunk_id=C_DUTOAN_2_1_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=P_DUTOAN_2_ID,
    hierarchy_level=1,
    node_type="TABLE_CHILD",
    heading_path=["Phụ lục Dự toán kinh phí năm 2026", "Mục I: Phần mềm và Bản quyền AI"],
    chunk_index=4,
    page_start=2,
    page_end=2,
    content_raw=(
        "| STT | NỘI DUNG | SỐ LƯỢNG | ĐƠN GIÁ | Thành tiền | Đài TNVN phê duyệt |\n"
        "|---|---|---|---|---|---|\n"
        "| I | Phần mềm | | | 84.600.000 | 84.600.000 |\n"
        "| 1 | Phần mềm Plagiarism Checker X 2025 Business | 1 | 7.200.000 | 7.200.000 | 7.200.000 |\n"
        "| 2 | Phần mềm Second Copy | 3 | 2.500.000 | 7.500.000 | 7.500.000 |\n"
        "| 3 | Phần mềm (Google Workspace Business Standard) gồm: Notebooklm AI | 3 | 4.300.000 | 12.900.000 | 12.900.000 |\n"
        "| 4 | Thư viện pháp luật (Basic 3) | 1 | 4.000.000 | 4.000.000 | 4.000.000 |\n"
        "| 5 | Phần mềm ChatGPT Business - Yearly Subscription | 5 | 10.600.000 | 53.000.000 | 53.000.000 |"
    ),
    keywords=["Phần mềm", "Plagiarism Checker X", "Second Copy", "Google Workspace", "Notebooklm AI", "ChatGPT Business", "7.200.000", "53.000.000", "12.900.000", "Thư viện pháp luật"],
)

CHILD_DUTOAN_2_2 = BenchmarkChunk(
    chunk_id=C_DUTOAN_2_2_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=P_DUTOAN_2_ID,
    hierarchy_level=1,
    node_type="TABLE_CHILD",
    heading_path=["Phụ lục Dự toán kinh phí năm 2026", "Mục II-V: Hội nghị và Tuyên truyền"],
    chunk_index=5,
    page_start=3,
    page_end=3,
    content_raw=(
        "| II | Hội nghị, hội thảo | 6 | 16.350.000 | 98.100.000 | 98.100.000 |\n"
        "| III | Chi cho các Hội đồng tư vấn, tuyển chọn, thẩm định | | | 152.400.000 | 152.400.000 |\n"
        "| IV | Kinh phí cho chương trình tuyên truyền về KH&CN trên sóng phát thanh | 1 | 90.000.000 | 90.000.000 | 90.000.000 |\n"
        "| V | Kinh phí cho chương trình tuyên truyền về KH&CN trên Báo điện tử | 1 | 74.900.000 | 74.900.000 | 74.900.000 |\n"
        "| | TỔNG CỘNG | | | 500.000.000 | 500.000.000 |"
    ),
    keywords=["Hội nghị", "Hội đồng tư vấn", "sóng phát thanh", "Báo điện tử", "tuyên truyền", "90.000.000", "74.900.000", "152.400.000", "500.000.000"],
)

# Chunks for DOC_NHANSU_80 (Chấm dứt hợp đồng làm việc bà Cao Thị Hoa Hương)
PARENT_NHANSU_1 = BenchmarkChunk(
    chunk_id=P_NHANSU_1_ID,
    document_id=DOC_NHANSU_ID,
    parent_id=None,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định chấm dứt hợp đồng làm việc"],
    chunk_index=0,
    page_start=1,
    page_end=1,
    content_raw=(
        "BAN CHẤP HÀNH TRUNG ƯƠNG ĐÀI TIẾNG NÓI VIỆT NAM - Số 80 -QĐ/TNVN, Hà Nội, ngày 28 tháng 4 năm 2026.\n"
        "QUYẾT ĐỊNH về chấm dứt hợp đồng làm việc đối với viên chức.\n"
        "Căn cứ Quyết định 16-QĐ/TW, ngày 30/3/2026 của Bộ Chính trị về chức năng, nhiệm vụ, tổ chức bộ máy của Đài Tiếng nói Việt Nam;\n"
        "Căn cứ Nghị định số 115/2020/NĐ-CP ngày 25/9/2020 của Chính phủ quy định về tuyển dụng, sử dụng và quản lý viên chức; "
        "Nghị định số 85/2023/NĐ-CP ngày 07/12/2023 của Chính phủ;\n"
        "Xét đề nghị của Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế tại Tờ trình số 329-TTr/TCCBHTQT ngày 22/4/2026.\n"
        "TỔNG GIÁM ĐỐC ĐÀI TIẾNG NÓI VIỆT NAM QUYẾT ĐỊNH:\n"
        "Điều 1. Chấm dứt hợp đồng làm việc đối với bà Cao Thị Hoa Hương, chuyên viên Đài phát sóng Đối ngoại, Trung tâm Kỹ thuật thuộc Đài Tiếng nói Việt Nam, kể từ ngày 01/5/2026.\n"
        "Điều 2. Bà Cao Thị Hoa Hương được hưởng chế độ bảo hiểm xã hội và chế độ khác theo quy định của pháp luật.\n"
        "Điều 3. Chánh Văn phòng, Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế, Trưởng ban Ban Kế hoạch - Tài chính, Giám đốc Trung tâm Kỹ thuật, "
        "Thủ trưởng các đơn vị có liên quan và bà Cao Thị Hoa Hương chịu trách nhiệm thi hành Quyết định này."
    ),
)

CHILD_NHANSU_1_1 = BenchmarkChunk(
    chunk_id=C_NHANSU_1_1_ID,
    document_id=DOC_NHANSU_ID,
    parent_id=P_NHANSU_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định chấm dứt hợp đồng làm việc", "Căn cứ pháp lý"],
    chunk_index=1,
    page_start=1,
    page_end=1,
    content_raw=(
        "Căn cứ Quyết định 16-QĐ/TW ngày 30/3/2026 của Bộ Chính trị về chức năng, nhiệm vụ, tổ chức bộ máy của Đài Tiếng nói Việt Nam; "
        "Căn cứ Nghị định số 115/2020/NĐ-CP ngày 25/9/2020 của Chính phủ quy định về tuyển dụng, sử dụng và quản lý viên chức; "
        "Nghị định số 85/2023/NĐ-CP ngày 07/12/2023 của Chính phủ; "
        "Xét đề nghị của Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế tại Tờ trình số 329-TTr/TCCBHTQT ngày 22/4/2026."
    ),
    keywords=["Căn cứ", "Quyết định 16-QĐ/TW", "Nghị định 115", "115/2020/NĐ-CP", "Tờ trình 329", "TCCBHTQT", "Ban Tổ chức cán bộ"],
)

CHILD_NHANSU_1_2 = BenchmarkChunk(
    chunk_id=C_NHANSU_1_2_ID,
    document_id=DOC_NHANSU_ID,
    parent_id=P_NHANSU_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định chấm dứt hợp đồng làm việc", "Điều 1-3: Chấm dứt HĐ và quyền lợi"],
    chunk_index=2,
    page_start=1,
    page_end=1,
    content_raw=(
        "Điều 1. Chấm dứt hợp đồng làm việc đối với bà Cao Thị Hoa Hương, chuyên viên Đài phát sóng Đối ngoại, Trung tâm Kỹ thuật thuộc Đài Tiếng nói Việt Nam, kể từ ngày 01/5/2026.\n"
        "Điều 2. Bà Cao Thị Hoa Hương được hưởng chế độ bảo hiểm xã hội và chế độ khác theo quy định của pháp luật.\n"
        "Điều 3. Chánh Văn phòng, Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế, Trưởng ban Ban Kế hoạch - Tài chính, Giám đốc Trung tâm Kỹ thuật và bà Cao Thị Hoa Hương chịu trách nhiệm thi hành Quyết định này."
    ),
    keywords=["Điều 1", "Điều 2", "Điều 3", "80-QĐ/TNVN", "Cao Thị Hoa Hương", "Đài phát sóng Đối ngoại", "Trung tâm Kỹ thuật", "chấm dứt hợp đồng", "bảo hiểm xã hội", "Ban Kế hoạch - Tài chính"],
)

# Chunks for DOC_QUYCHE_587 (Liên hoan Phát thanh toàn quốc 2026)
PARENT_QUYCHE_1 = BenchmarkChunk(
    chunk_id=P_QUYCHE_1_ID,
    document_id=DOC_QUYCHE_ID,
    parent_id=None,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định ban hành Quy chế chấm điểm"],
    chunk_index=0,
    page_start=1,
    page_end=1,
    content_raw=(
        "Số: 587 /QĐ-TNVN, Hà Nội, ngày 16 tháng 3 năm 2026.\n"
        "QUYẾT ĐỊNH Về việc ban hành Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026.\n"
        "Căn cứ Nghị định số 46/2025/NĐ-CP ngày 28/02/2025 của Chính phủ quy định chức năng, nhiệm vụ, quyền hạn và cơ cấu tổ chức của Đài Tiếng nói Việt Nam;\n"
        "Căn cứ Quyết định số 2214/QĐ-TNVN ngày 07/7/2025 của Tổng Giám đốc Đài Tiếng nói Việt Nam về việc tổ chức Liên hoan Phát thanh toàn quốc lần thứ XVII năm 2026;\n"
        "Điều 1. Ban hành kèm theo Quyết định này Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026.\n"
        "Điều 2. Quyết định này có hiệu lực kể từ ngày ký.\n"
        "Điều 3. Chánh Văn phòng, Trưởng ban Ban Thư ký biên tập, Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế, Trưởng ban Ban Kế hoạch - Tài chính và các thành viên Hội đồng Giám khảo chịu trách nhiệm thi hành Quyết định này."
    ),
)

CHILD_QUYCHE_1_1 = BenchmarkChunk(
    chunk_id=C_QUYCHE_1_1_ID,
    document_id=DOC_QUYCHE_ID,
    parent_id=P_QUYCHE_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định ban hành Quy chế chấm điểm", "Điều 1: Ban hành Quy chế"],
    chunk_index=1,
    page_start=1,
    page_end=1,
    content_raw=(
        "QUYẾT ĐỊNH Về việc ban hành Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026.\n"
        "Căn cứ Nghị định số 46/2025/NĐ-CP của Chính phủ; Căn cứ Quyết định số 2214/QĐ-TNVN của Tổng Giám đốc Đài Tiếng nói Việt Nam;\n"
        "Điều 1. Ban hành kèm theo Quyết định này Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026."
    ),
    keywords=["587/QĐ-TNVN", "Quy chế chấm điểm", "Liên hoan Phát thanh toàn quốc", "Quảng Ninh 2026", "lần thứ XVII", "Điều 1"],
)

CHILD_QUYCHE_1_2 = BenchmarkChunk(
    chunk_id=C_QUYCHE_1_2_ID,
    document_id=DOC_QUYCHE_ID,
    parent_id=P_QUYCHE_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định ban hành Quy chế chấm điểm", "Điều 2 & Điều 3: Hiệu lực thi hành"],
    chunk_index=2,
    page_start=1,
    page_end=1,
    content_raw=(
        "Điều 2. Quyết định này có hiệu lực kể từ ngày ký.\n"
        "Điều 3. Chánh Văn phòng, Trưởng ban Ban Thư ký biên tập, Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế, Trưởng ban Ban Kế hoạch - Tài chính, "
        "Thủ trưởng các đơn vị có liên quan và các thành viên Hội đồng Giám khảo Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026 chịu trách nhiệm thi hành Quyết định này."
    ),
    keywords=["Điều 2", "Điều 3", "Ban Thư ký biên tập", "Ban Kế hoạch - Tài chính", "Hội đồng Giám khảo", "thi hành", "hiệu lực"],
)

PARENT_QUYCHE_2 = BenchmarkChunk(
    chunk_id=P_QUYCHE_2_ID,
    document_id=DOC_QUYCHE_ID,
    parent_id=None,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quy chế chấm điểm", "Điều kiện tham dự & Thể loại"],
    chunk_index=3,
    page_start=2,
    page_end=3,
    content_raw=(
        "QUY CHẾ CHẤM ĐIỂM TÁC PHẨM THAM DỰ LIÊN HOAN PHÁT THANH TOÀN QUỐC LẦN THỨ XVII - QUẢNG NINH 2026.\n"
        "Điều 1: ĐIỀU KIỆN THAM DỰ.\n"
        "1. Điều kiện về tác giả: Tác giả là phóng viên, biên tập viên, phát thanh viên, kỹ thuật viên thuộc Đài Tiếng nói Việt Nam "
        "và các Đài Phát thanh - Truyền hình, Trung tâm Truyền thông tỉnh, thành phố trực thuộc Trung ương trong cả nước.\n"
        "2. Thể loại tác phẩm tham dự: Bao gồm Phóng sự, Phỏng vấn, Câu chuyện truyền thanh, Kịch truyền thanh, Chương trình phát thanh trực tiếp.\n"
        "Tác phẩm phải có tính định hướng xã hội cao, tiếng động hiện trường trung thực, sống động, ứng dụng công nghệ phát thanh hiện đại."
    ),
)

CHILD_QUYCHE_2_1 = BenchmarkChunk(
    chunk_id=C_QUYCHE_2_1_ID,
    document_id=DOC_QUYCHE_ID,
    parent_id=P_QUYCHE_2_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quy chế chấm điểm", "Điều 1: Tác giả và Thể loại"],
    chunk_index=4,
    page_start=2,
    page_end=2,
    content_raw=(
        "Điều 1: ĐIỀU KIỆN THAM DỰ LIÊN HOAN PHÁT THANH TOÀN QUỐC 2026.\n"
        "1. Điều kiện về tác giả: Tác giả là phóng viên, biên tập viên, phát thanh viên, kỹ thuật viên thuộc Đài Tiếng nói Việt Nam và các cơ quan truyền thông cả nước.\n"
        "2. Thể loại tác phẩm tham dự: Phóng sự, Phỏng vấn, Câu chuyện truyền thanh, Kịch truyền thanh, Chương trình phát thanh trực tiếp. "
        "Tác phẩm phải có tính định hướng xã hội cao, âm thanh và tiếng động hiện trường trung thực, sinh động."
    ),
    keywords=["Điều kiện tham dự", "tác giả", "phóng viên", "biên tập viên", "thể loại", "Phóng sự", "Phỏng vấn", "Kịch truyền thanh", "Chương trình phát thanh trực tiếp"],
)

# Chunks aliases for compatibility
PARENT_TECH_1 = PARENT_QUYCHE_1
CHILD_TECH_1_1 = CHILD_QUYCHE_1_1
CHILD_TECH_1_2 = CHILD_QUYCHE_1_2
PARENT_TECH_2 = PARENT_QUYCHE_2
CHILD_TECH_2_1 = CHILD_QUYCHE_2_1

PARENT_HR_1 = PARENT_NHANSU_1
CHILD_HR_1_1 = CHILD_NHANSU_1_1
CHILD_HR_1_2 = CHILD_NHANSU_1_2

PARENT_FIN_1 = PARENT_DUTOAN_1
CHILD_FIN_1_1 = CHILD_DUTOAN_2_1
CHILD_FIN_1_2 = CHILD_DUTOAN_1_1

BENCHMARK_CORPUS = BenchmarkCorpus(
    documents=[DOC_DUTOAN_427, DOC_NHANSU_80, DOC_QUYCHE_587],
    chunks=[
        PARENT_DUTOAN_1, CHILD_DUTOAN_1_1, CHILD_DUTOAN_1_2,
        PARENT_DUTOAN_2, CHILD_DUTOAN_2_1, CHILD_DUTOAN_2_2,
        PARENT_NHANSU_1, CHILD_NHANSU_1_1, CHILD_NHANSU_1_2,
        PARENT_QUYCHE_1, CHILD_QUYCHE_1_1, CHILD_QUYCHE_1_2,
        PARENT_QUYCHE_2, CHILD_QUYCHE_2_1,
    ],
)


# ---------------------------------------------------------------------------
# Standard Curated Benchmark Queries (All 11 Categories, Real Data)
# ---------------------------------------------------------------------------

BENCHMARK_QUERIES: list[BenchmarkQuery] = [
    # 1. EXACT_KEYWORD
    BenchmarkQuery(
        query_id="q01_exact_kw",
        category=BenchmarkQueryCategory.EXACT_KEYWORD,
        query_text="Quyết định 80-QĐ/TNVN chấm dứt hợp đồng",
        description="Truy vấn từ khóa chính xác về quyết định chấm dứt hợp đồng viên chức",
        expected_doc_ids=[DOC_NHANSU_ID],
        expected_parent_ids=[P_NHANSU_1_ID],
        expected_child_ids=[C_NHANSU_1_2_ID],
    ),
    # 2. SEMANTIC_PARAPHRASE
    BenchmarkQuery(
        query_id="q02_semantic_paraphrase",
        category=BenchmarkQueryCategory.SEMANTIC_PARAPHRASE,
        query_text="Chuyên viên thuộc trung tâm kỹ thuật thôi việc và giải quyết chế độ bảo hiểm xã hội",
        description="Diễn đạt tự nhiên việc viên chức nghỉ việc và hưởng quyền lợi bảo hiểm",
        expected_doc_ids=[DOC_NHANSU_ID],
        expected_parent_ids=[P_NHANSU_1_ID],
        expected_child_ids=[C_NHANSU_1_2_ID],
    ),
    # 3. MIXED_EN_VI
    BenchmarkQuery(
        query_id="q03_mixed_en_vi",
        category=BenchmarkQueryCategory.MIXED_EN_VI,
        query_text="Dự toán kinh phí phần mềm ChatGPT Business và Google Workspace Notebooklm AI",
        description="Truy vấn hỗn hợp tên phần mềm AI tiếng Anh và hạng mục ngân sách tiếng Việt",
        expected_doc_ids=[DOC_DUTOAN_ID],
        expected_parent_ids=[P_DUTOAN_2_ID],
        expected_child_ids=[C_DUTOAN_2_1_ID],
    ),
    # 4. HEADING_SPECIFIC
    BenchmarkQuery(
        query_id="q04_heading_specific",
        category=BenchmarkQueryCategory.HEADING_SPECIFIC,
        query_text="Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII Quảng Ninh 2026",
        description="Tìm kiếm theo tiêu đề quyết định ban hành quy chế chấm thi phát thanh",
        expected_doc_ids=[DOC_QUYCHE_ID],
        expected_parent_ids=[P_QUYCHE_1_ID],
        expected_child_ids=[C_QUYCHE_1_1_ID],
    ),
    # 5. TABLE_SPECIFIC
    BenchmarkQuery(
        query_id="q05_table_specific",
        category=BenchmarkQueryCategory.TABLE_SPECIFIC,
        query_text="Chi phí phần mềm Plagiarism Checker X 2025 Business và Second Copy trong phụ lục dự toán là bao nhiêu?",
        description="Truy vấn thông tin số liệu nằm trong bảng phụ lục kinh phí phần mềm",
        expected_doc_ids=[DOC_DUTOAN_ID],
        expected_parent_ids=[P_DUTOAN_2_ID],
        expected_child_ids=[C_DUTOAN_2_1_ID],
    ),
    # 6. DOCUMENT_LOCAL
    BenchmarkQuery(
        query_id="q06_doc_local",
        category=BenchmarkQueryCategory.DOCUMENT_LOCAL,
        query_text="Căn cứ Tờ trình số 329 của Ban Tổ chức cán bộ và Hợp tác quốc tế theo Nghị định 115",
        description="Truy vấn giới hạn phạm vi trong một văn bản quyết định nhân sự",
        mode=RetrievalMode.DOCUMENT_SEARCH,
        document_ids=[DOC_NHANSU_ID],
        expected_doc_ids=[DOC_NHANSU_ID],
        expected_parent_ids=[P_NHANSU_1_ID],
        expected_child_ids=[C_NHANSU_1_1_ID],
    ),
    # 7. CROSS_DOC_COMPARE
    BenchmarkQuery(
        query_id="q07_cross_compare",
        category=BenchmarkQueryCategory.CROSS_DOC_COMPARE,
        query_text="So sánh trách nhiệm thi hành của Ban Kế hoạch - Tài chính trong Quyết định 427 dự toán và Quyết định 80 nhân sự",
        description="So sánh chéo vai trò của Ban Kế hoạch - Tài chính giữa 2 quyết định khác nhau",
        mode=RetrievalMode.COMPARE_DOCUMENTS,
        document_ids=[DOC_DUTOAN_ID, DOC_NHANSU_ID],
        expected_doc_ids=[DOC_DUTOAN_ID, DOC_NHANSU_ID],
        expected_parent_ids=[P_DUTOAN_1_ID, P_NHANSU_1_ID],
        expected_child_ids=[C_DUTOAN_1_2_ID, C_NHANSU_1_2_ID],
    ),
    # 8. NEGATIVE_NO_ANSWER
    BenchmarkQuery(
        query_id="q08_negative_no_ans",
        category=BenchmarkQueryCategory.NEGATIVE_NO_ANSWER,
        query_text="Quy định về tiêu chuẩn bổ nhiệm chức danh Giáo sư và Phó giáo sư ngành phát thanh truyền hình",
        description="Truy vấn nội dung hoàn toàn không tồn tại trong các quyết định nội bộ",
        expected_doc_ids=[],
        expected_parent_ids=[],
        expected_child_ids=[],
        expected_sufficiency=SufficiencyStatus.INSUFFICIENT,
    ),
    # 9. SOURCE_FILTERED
    BenchmarkQuery(
        query_id="q09_source_filtered",
        category=BenchmarkQueryCategory.SOURCE_FILTERED,
        query_text="Phê duyệt dự toán kinh phí Hoạt động thông tin khoa học năm 2026",
        description="Lọc nguồn theo định dạng file md của kho tài liệu quyết định",
        source_filters={"source_type": "md"},
        expected_doc_ids=[DOC_DUTOAN_ID],
        expected_parent_ids=[P_DUTOAN_1_ID],
        expected_child_ids=[C_DUTOAN_1_1_ID],
    ),
    # 10. BROAD_WHY_EXPLAIN
    BenchmarkQuery(
        query_id="q10_broad_why_explain",
        category=BenchmarkQueryCategory.BROAD_WHY_EXPLAIN,
        query_text="Giải thích quy định về điều kiện tác giả phóng viên biên tập viên và thể loại tác phẩm tham dự Liên hoan Phát thanh 2026",
        description="Câu hỏi tổng quan giải thích điều kiện và thể loại tham gia liên hoan",
        expected_doc_ids=[DOC_QUYCHE_ID],
        expected_parent_ids=[P_QUYCHE_2_ID],
        expected_child_ids=[C_QUYCHE_2_1_ID],
    ),
    # 11. SPECIFIC_FACT
    BenchmarkQuery(
        query_id="q11_specific_fact",
        category=BenchmarkQueryCategory.SPECIFIC_FACT,
        query_text="Tổng số tiền phê duyệt dự toán Hoạt động thông tin khoa học Đài TNVN năm 2026 là bao nhiêu đồng?",
        description="Truy vấn số liệu sự thật chính xác tuyệt đối (500.000.000 đồng)",
        expected_doc_ids=[DOC_DUTOAN_ID],
        expected_parent_ids=[P_DUTOAN_1_ID],
        expected_child_ids=[C_DUTOAN_1_1_ID],
    ),
]
