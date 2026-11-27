"""Benchmark queries covering all 11 categories, with their ground-truth ids."""

from __future__ import annotations

from app.domain.models.retrieval import RetrievalMode
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from tests.fixtures.retrieval_benchmark.ids import (
    C_CHITHI_1838_1_ID,
    C_DAOTAO_50_1_ID,
    C_DAOTAO_87_1_ID,
    C_DUTOAN_1_1_ID,
    C_DUTOAN_1_2_ID,
    C_DUTOAN_2_1_ID,
    C_NHANSU_1_1_ID,
    C_NHANSU_1_2_ID,
    C_NHANSU_109_1_ID,
    C_NHANSU_862_1_ID,
    C_PHATSONG_72_1_ID,
    C_PHATSONG_2244_1_ID,
    C_QUYCHE_2_1_ID,
    C_THIDUA_1119_1_ID,
    DOC_CHITHI_1838_ID,
    DOC_DAOTAO_50_ID,
    DOC_DAOTAO_87_ID,
    DOC_DUTOAN_ID,
    DOC_NHANSU_109_ID,
    DOC_NHANSU_862_ID,
    DOC_NHANSU_ID,
    DOC_PHATSONG_72_ID,
    DOC_PHATSONG_2244_ID,
    DOC_QUYCHE_ID,
    DOC_THIDUA_1119_ID,
    P_CHITHI_1838_ID,
    P_DAOTAO_50_ID,
    P_DAOTAO_87_ID,
    P_DUTOAN_1_ID,
    P_DUTOAN_2_ID,
    P_NHANSU_1_ID,
    P_NHANSU_109_ID,
    P_NHANSU_862_ID,
    P_PHATSONG_72_ID,
    P_PHATSONG_2244_ID,
    P_QUYCHE_2_ID,
    P_THIDUA_1119_ID,
)
from tests.fixtures.retrieval_benchmark_types import (
    BenchmarkQuery,
    BenchmarkQueryCategory,
)

# ---------------------------------------------------------------------------
# Comprehensive Benchmark Queries (All 11 Categories + Multi-Doc Queries)
# ---------------------------------------------------------------------------

BENCHMARK_QUERIES: list[BenchmarkQuery] = [
    # 1. EXACT_KEYWORD
    BenchmarkQuery(
        query_id="q01_exact_kw",
        category=BenchmarkQueryCategory.EXACT_KEYWORD,
        query_text="Quyết định 80-QĐ/TNVN chấm dứt hợp đồng bà Cao Thị Hoa Hương",
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
        query_text="Tuyển dụng bà Hoàng Phương Ly về làm việc tại Ban Đối ngoại VOV5",
        description="Truy vấn theo tiêu đề và nội dung tuyển dụng nhân sự VOV5",
        expected_doc_ids=[DOC_NHANSU_109_ID],
        expected_parent_ids=[P_NHANSU_109_ID],
        expected_child_ids=[C_NHANSU_109_1_ID],
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
    # 7. CROSS_DOC_COMPARE: Ban KHTC trong QĐ 427 và QĐ 80
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
    # 12. MULTI-DOC: Phó TGĐ Vũ Hải Quang ký những văn bản nào?
    BenchmarkQuery(
        query_id="q12_multi_doc_signer_vhq",
        category=BenchmarkQueryCategory.CROSS_DOC_COMPARE,
        query_text="Phó Tổng Giám đốc Vũ Hải Quang đã ký những quyết định nào về dự toán kỹ thuật phát sóng và thâm niên?",
        description="Truy vấn đa tài liệu: tìm các văn bản do Phó Tổng Giám đốc Vũ Hải Quang ký",
        expected_doc_ids=[
            DOC_DUTOAN_ID,
            DOC_NHANSU_ID,
            DOC_PHATSONG_2244_ID,
            DOC_PHATSONG_72_ID,
            DOC_NHANSU_862_ID,
        ],
        expected_parent_ids=[
            P_DUTOAN_1_ID,
            P_NHANSU_1_ID,
            P_PHATSONG_2244_ID,
            P_PHATSONG_72_ID,
            P_NHANSU_862_ID,
        ],
        expected_child_ids=[
            C_DUTOAN_1_1_ID,
            C_NHANSU_1_2_ID,
            C_PHATSONG_2244_1_ID,
            C_PHATSONG_72_1_ID,
            C_NHANSU_862_1_ID,
        ],
    ),
    # 13. MULTI-DOC: Tổng Giám đốc Đỗ Tiến Sỹ ký những văn bản nào?
    BenchmarkQuery(
        query_id="q13_multi_doc_signer_dts",
        category=BenchmarkQueryCategory.CROSS_DOC_COMPARE,
        query_text="Tổng Giám đốc Đỗ Tiến Sỹ đã ký những quyết định nào về tuyển dụng nhân sự và thi đua khen thưởng?",
        description="Truy vấn đa tài liệu: tìm các văn bản do Tổng Giám đốc Đỗ Tiến Sỹ ký",
        expected_doc_ids=[DOC_NHANSU_109_ID, DOC_THIDUA_1119_ID],
        expected_parent_ids=[P_NHANSU_109_ID, P_THIDUA_1119_ID],
        expected_child_ids=[C_NHANSU_109_1_ID, C_THIDUA_1119_1_ID],
    ),
    # 14. MULTI-DOC: Phó TGĐ Ngô Minh Hiển ký những văn bản nào?
    BenchmarkQuery(
        query_id="q14_multi_doc_signer_nmh",
        category=BenchmarkQueryCategory.CROSS_DOC_COMPARE,
        query_text="Phó Tổng Giám đốc Ngô Minh Hiển đã ký các văn bản nào về tập huấn AI và chỉ thị?",
        description="Truy vấn đa tài liệu: tìm các văn bản do Ngô Minh Hiển ký",
        expected_doc_ids=[DOC_DAOTAO_87_ID, DOC_CHITHI_1838_ID],
        expected_parent_ids=[P_DAOTAO_87_ID, P_CHITHI_1838_ID],
        expected_child_ids=[C_DAOTAO_87_1_ID, C_CHITHI_1838_1_ID],
    ),
    # 15. MULTI-DOC: Trí tuệ nhân tạo (AI) trong nhiều văn bản
    BenchmarkQuery(
        query_id="q15_multi_doc_topic_ai",
        category=BenchmarkQueryCategory.CROSS_DOC_COMPARE,
        query_text="Những văn bản quyết định nào của Đài Tiếng nói Việt Nam có nội dung về Trí tuệ nhân tạo AI hoặc phần mềm AI?",
        description="Truy vấn đa tài liệu liên kết chuyên đề AI (dự toán QĐ 427 và tập huấn QĐ 87)",
        expected_doc_ids=[DOC_DUTOAN_ID, DOC_DAOTAO_87_ID],
        expected_parent_ids=[P_DUTOAN_2_ID, P_DAOTAO_87_ID],
        expected_child_ids=[C_DUTOAN_2_1_ID, C_DAOTAO_87_1_ID],
    ),
    # 16. MULTI-DOC: Trạm phát sóng Cột 5 Quảng Ninh
    BenchmarkQuery(
        query_id="q16_multi_doc_phatsong_quangninh",
        category=BenchmarkQueryCategory.CROSS_DOC_COMPARE,
        query_text="Các quyết định nào điều chỉnh phương án phát sóng FM tại trạm phát sóng Cột 5 phường Hạ Long Quảng Ninh do ai ký?",
        description="Truy vấn đa tài liệu về trạm phát sóng Cột 5 Quảng Ninh (QĐ 2244 và QĐ 72 do Vũ Hải Quang ký)",
        expected_doc_ids=[DOC_PHATSONG_2244_ID, DOC_PHATSONG_72_ID],
        expected_parent_ids=[P_PHATSONG_2244_ID, P_PHATSONG_72_ID],
        expected_child_ids=[C_PHATSONG_2244_1_ID, C_PHATSONG_72_1_ID],
    ),
    # 17. MULTI-DOC: Phó TGĐ Phạm Mạnh Hùng ký văn bản nào?
    BenchmarkQuery(
        query_id="q17_multi_doc_signer_pmh",
        category=BenchmarkQueryCategory.CROSS_DOC_COMPARE,
        query_text="Phó Tổng Giám đốc Phạm Mạnh Hùng đã ký quyết định nào về cử viên chức tham gia các lớp bồi dưỡng chức danh nghề nghiệp?",
        description="Truy vấn văn bản do Phó Tổng Giám đốc Phạm Mạnh Hùng ký (QĐ 50)",
        expected_doc_ids=[DOC_DAOTAO_50_ID],
        expected_parent_ids=[P_DAOTAO_50_ID],
        expected_child_ids=[C_DAOTAO_50_1_ID],
    ),
    # 18. MULTI-DOC: Chế độ thâm niên vượt khung 2026
    BenchmarkQuery(
        query_id="q18_multi_doc_thamnien_2026",
        category=BenchmarkQueryCategory.CROSS_DOC_COMPARE,
        query_text="Văn bản nào quy định về việc thực hiện chế độ thâm niên vượt khung năm 2026 cho cán bộ viên chức và do ai ký?",
        description="Truy vấn văn bản về thâm niên vượt khung (QĐ 862 do Vũ Hải Quang ký)",
        expected_doc_ids=[DOC_NHANSU_862_ID],
        expected_parent_ids=[P_NHANSU_862_ID],
        expected_child_ids=[C_NHANSU_862_1_ID],
    ),
]
