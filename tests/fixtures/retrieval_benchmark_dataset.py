"""Curated retrieval benchmark corpus (spec P10-21). Kept out of app/ production imports."""

from __future__ import annotations

import uuid

from app.domain.models.retrieval import RetrievalMode
from app.domain.models.retrieval.sufficiency import SufficiencyStatus
from app.services.retrieval.benchmark_types import (
    BenchmarkChunk,
    BenchmarkCorpus,
    BenchmarkDocument,
    BenchmarkQuery,
    BenchmarkQueryCategory,
)

# ---------------------------------------------------------------------------
# Deterministic UUID generator for normative identifiers
# ---------------------------------------------------------------------------


def _bench_uuid(name: str) -> str:
    """Generate deterministic RFC 4122 UUID compliant with sql.py uuid_literal."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"personal_ai_assistant.benchmark.{name}"))


# ---------------------------------------------------------------------------
# Real Document UUIDs (from data/QuyetDinh across all 8 subdirectories)
# ---------------------------------------------------------------------------

# 1. DuToan 427/QĐ-TNVN (Ký: Vũ Hải Quang)
DOC_DUTOAN_ID = _bench_uuid("doc.dutoan.427")
P_DUTOAN_1_ID = _bench_uuid("parent.dutoan.1")
C_DUTOAN_1_1_ID = _bench_uuid("child.dutoan.1.1")
C_DUTOAN_1_2_ID = _bench_uuid("child.dutoan.1.2")
P_DUTOAN_2_ID = _bench_uuid("parent.dutoan.2")
C_DUTOAN_2_1_ID = _bench_uuid("child.dutoan.2.1")
C_DUTOAN_2_2_ID = _bench_uuid("child.dutoan.2.2")

# 2. NhanSu 80-QĐ/TNVN (Ký: Vũ Hải Quang)
DOC_NHANSU_ID = _bench_uuid("doc.nhansu.80")
P_NHANSU_1_ID = _bench_uuid("parent.nhansu.1")
C_NHANSU_1_1_ID = _bench_uuid("child.nhansu.1.1")
C_NHANSU_1_2_ID = _bench_uuid("child.nhansu.1.2")

# 3. QuyChe 587/QĐ-TNVN (Liên hoan Phát thanh XVII Quảng Ninh 2026)
DOC_QUYCHE_ID = _bench_uuid("doc.quyche.587")
P_QUYCHE_1_ID = _bench_uuid("parent.quyche.1")
C_QUYCHE_1_1_ID = _bench_uuid("child.quyche.1.1")
C_QUYCHE_1_2_ID = _bench_uuid("child.quyche.1.2")
P_QUYCHE_2_ID = _bench_uuid("parent.quyche.2")
C_QUYCHE_2_1_ID = _bench_uuid("child.quyche.2.1")

# 4. DaoTao 87/QĐ-TNVN (Ứng dụng AI trong tòa soạn - Ký: Ngô Minh Hiển)
DOC_DAOTAO_87_ID = _bench_uuid("doc.daotao.87")
P_DAOTAO_87_ID = _bench_uuid("parent.daotao.87")
C_DAOTAO_87_1_ID = _bench_uuid("child.daotao.87.1")

# 5. NhanSu 109-QĐ/TNVN (Tuyển dụng VOV5 - Ký: Đỗ Tiến Sỹ)
DOC_NHANSU_109_ID = _bench_uuid("doc.nhansu.109")
P_NHANSU_109_ID = _bench_uuid("parent.nhansu.109")
C_NHANSU_109_1_ID = _bench_uuid("child.nhansu.109.1")

# 6. PhatSong 2244/QĐ-TNVN (Trạm phát sóng Cột 5 Quảng Ninh - Ký: Vũ Hải Quang)
DOC_PHATSONG_2244_ID = _bench_uuid("doc.phatsong.2244")
P_PHATSONG_2244_ID = _bench_uuid("parent.phatsong.2244")
C_PHATSONG_2244_1_ID = _bench_uuid("child.phatsong.2244.1")

# 7. PhatSong 72/QĐ-TNVN (VOV Giao thông Cột 5 Quảng Ninh - Ký: Vũ Hải Quang)
DOC_PHATSONG_72_ID = _bench_uuid("doc.phatsong.72")
P_PHATSONG_72_ID = _bench_uuid("parent.phatsong.72")
C_PHATSONG_72_1_ID = _bench_uuid("child.phatsong.72.1")

# 8. ThiDua 1119/QĐ-TNVN (Bằng khen Liên hoan Phát thanh 2026 - Ký: Đỗ Tiến Sỹ)
DOC_THIDUA_1119_ID = _bench_uuid("doc.thidua.1119")
P_THIDUA_1119_ID = _bench_uuid("parent.thidua.1119")
C_THIDUA_1119_1_ID = _bench_uuid("child.thidua.1119.1")

# 9. NhanSu 862/QĐ-TNVN (Thâm niên vượt khung 2026 - Ký: Vũ Hải Quang)
DOC_NHANSU_862_ID = _bench_uuid("doc.nhansu.862")
P_NHANSU_862_ID = _bench_uuid("parent.nhansu.862")
C_NHANSU_862_1_ID = _bench_uuid("child.nhansu.862.1")

# 10. ChiThi 1838/CT-TNVN (Diễn đàn EVFTA - Ký: Ngô Minh Hiển)
DOC_CHITHI_1838_ID = _bench_uuid("doc.chithi.1838")
P_CHITHI_1838_ID = _bench_uuid("parent.chithi.1838")
C_CHITHI_1838_1_ID = _bench_uuid("child.chithi.1838.1")

# 11. DaoTao 50/QĐ-TNVN (Bồi dưỡng chuyên viên - Ký: Phạm Mạnh Hùng)
DOC_DAOTAO_50_ID = _bench_uuid("doc.daotao.50")
P_DAOTAO_50_ID = _bench_uuid("parent.daotao.50")
C_DAOTAO_50_1_ID = _bench_uuid("child.daotao.50.1")

# Aliases for backwards compatibility
DOC_FIN_ID = DOC_DUTOAN_ID
DOC_HR_ID = DOC_NHANSU_ID
DOC_TECH_ID = DOC_QUYCHE_ID


# ---------------------------------------------------------------------------
# Real Document Models
# ---------------------------------------------------------------------------

DOC_DUTOAN_427 = BenchmarkDocument(
    document_id=DOC_DUTOAN_ID,
    title="Quyết định 427/QĐ-TNVN ngày 25/02/2026 phê duyệt dự toán kinh phí Hoạt động thông tin khoa học năm 2026",
    uri="data/QuyetDinh/DuToan/18-3-2026-954776_427QD_25_02_2026.md",
    source_type="md",
    description="Dự toán kinh phí 500 triệu đồng kèm bảng phụ lục phần mềm ChatGPT, NotebookLM và bản quyền AI do Phó Tổng Giám đốc Vũ Hải Quang ký",
)

DOC_NHANSU_80 = BenchmarkDocument(
    document_id=DOC_NHANSU_ID,
    title="Quyết định 80-QĐ/TNVN ngày 28/04/2026 về việc chấm dứt hợp đồng làm việc đối với viên chức",
    uri="data/QuyetDinh/NhanSu.TienLuong/14-5-2026-1125655_80QD_28_04_2026.md",
    source_type="md",
    description="Chấm dứt HĐ làm việc đối với bà Cao Thị Hoa Hương (Trung tâm Kỹ thuật) hưởng BHXH do Phó Tổng Giám đốc Vũ Hải Quang ký",
)

DOC_QUYCHE_587 = BenchmarkDocument(
    document_id=DOC_QUYCHE_ID,
    title="Quyết định 587/QĐ-TNVN ngày 16/03/2026 ban hành Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026",
    uri="data/QuyetDinh/QuyChe.QuyDinh/19-3-2026-158402_587QD_16_03_2026.md",
    source_type="md",
    description="Quy chế chấm điểm Liên hoan Phát thanh 2026, điều kiện tác giả phóng viên biên tập viên",
)

DOC_DAOTAO_87 = BenchmarkDocument(
    document_id=DOC_DAOTAO_87_ID,
    title="Quyết định 87/QĐ-TNVN ngày 29/04/2026 về tổ chức Khóa Tập huấn nghiệp vụ: Ứng dụng AI trong tòa soạn",
    uri="data/QuyetDinh/DaoTao/14-5-2026-1129466_87QD_29_04_2026.md",
    source_type="md",
    description="Tập huấn Ứng dụng AI trong tòa soạn đón chuyên gia Hãng thông tấn Sputnik Nga do Phó Tổng Giám đốc Ngô Minh Hiển ký",
)

DOC_NHANSU_109 = BenchmarkDocument(
    document_id=DOC_NHANSU_109_ID,
    title="Quyết định 109-QĐ/TNVN ngày 05/05/2026 về việc tuyển dụng viên chức Ban Đối ngoại VOV5",
    uri="data/QuyetDinh/NhanSu.TienLuong/14-5-2026-1135538_109QD_05_05_2026.md",
    source_type="md",
    description="Tuyển dụng bà Hoàng Phương Ly về làm việc tại Ban Đối ngoại VOV5 do Tổng Giám đốc Đỗ Tiến Sỹ ký",
)

DOC_PHATSONG_2244 = BenchmarkDocument(
    document_id=DOC_PHATSONG_2244_ID,
    title="Quyết định 2244/QĐ-TNVN ngày 09/07/2025 về điều chỉnh phương án phát sóng FM tại trạm phát sóng Cột 5 Hạ Long Quảng Ninh",
    uri="data/QuyetDinh/PhatSong/17-7-2025-1626637_2244QD_09_07_2025.md",
    source_type="md",
    description="Điều chỉnh công suất và đơn giá phát sóng FM trạm Cột 5 Hạ Long Quảng Ninh do Phó Tổng Giám đốc Vũ Hải Quang ký",
)

DOC_PHATSONG_72 = BenchmarkDocument(
    document_id=DOC_PHATSONG_72_ID,
    title="Quyết định 72/QĐ-TNVN ngày 15/01/2026 về phát sóng FM Kênh VOV Giao thông Duyên Hải tại Trạm phát sóng Cột 5 phường Hạ Long Quảng Ninh",
    uri="data/QuyetDinh/PhatSong/29-1-2026-1438859_72QD_15_01_2026.md",
    source_type="md",
    description="Phát sóng FM Kênh VOV Giao thông Duyên Hải tần số 91.5 MHz công suất 10kW trạm Cột 5 do Phó Tổng Giám đốc Vũ Hải Quang ký",
)

DOC_THIDUA_1119 = BenchmarkDocument(
    document_id=DOC_THIDUA_1119_ID,
    title="Quyết định 1119/QĐ-TNVN ngày 13/04/2026 về việc tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam",
    uri="data/QuyetDinh/ThiDuaKhenThuong/6-5-2026-1626379_1119QD_13_04_2026.md",
    source_type="md",
    description="Tặng Bằng khen cho các tập thể xuất sắc tại Liên hoan Phát thanh toàn quốc lần thứ XVII Quảng Ninh do Tổng Giám đốc Đỗ Tiến Sỹ ký",
)

DOC_NHANSU_862 = BenchmarkDocument(
    document_id=DOC_NHANSU_862_ID,
    title="Quyết định 862/QĐ-TNVN ngày 31/03/2026 về việc thực hiện chế độ thâm niên vượt khung năm 2026",
    uri="data/QuyetDinh/NhanSu.TienLuong/21-4-2026-1452152_862QD_31_3_2026.md",
    source_type="md",
    description="Thực hiện chế độ phụ cấp thâm niên vượt khung đối với ông Dương Văn Đoàn do Phó Tổng Giám đốc Vũ Hải Quang ký",
)

DOC_CHITHI_1838 = BenchmarkDocument(
    document_id=DOC_CHITHI_1838_ID,
    title="Chỉ thị 1838/CT-TNVN ngày 23/07/2020 về việc tổ chức Diễn đàn trực tuyến EVFTA",
    uri="data/QuyetDinh/ChiThi/30-8-2020-1431349_CT1838_23_07_2020.md",
    source_type="md",
    description="Chỉ thị tổ chức Diễn đàn trực tuyến Hiệp định thương mại tự do EVFTA do Phó Tổng Giám đốc Ngô Minh Hiển ký",
)

DOC_DAOTAO_50 = BenchmarkDocument(
    document_id=DOC_DAOTAO_50_ID,
    title="Quyết định 50/QĐ-TNVN ngày 23/04/2026 về cử viên chức tham gia các lớp Bồi dưỡng ngạch chuyên viên và chuyên viên chính năm 2026",
    uri="data/QuyetDinh/DaoTao/14-5-2026-163037_50QD_23_04_2026.md",
    source_type="md",
    description="Cử viên chức tham gia các lớp bồi dưỡng ngạch chuyên viên và chuyên viên chính do Phó Tổng Giám đốc Phạm Mạnh Hùng ký",
)


# ---------------------------------------------------------------------------
# Real Document Chunks
# ---------------------------------------------------------------------------

# 1. Chunks for QĐ 427
PARENT_DUTOAN_1 = BenchmarkChunk(
    chunk_id=P_DUTOAN_1_ID,
    document_id=DOC_DUTOAN_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 427/QĐ-TNVN", "Điều 1: Phê duyệt dự toán"],
    content_raw="Số: 427 /QĐ-TNVN ngày 25 tháng 02 năm 2026. Phê duyệt dự toán kinh phí Hoạt động thông tin khoa học năm 2026 của Đài Tiếng nói Việt Nam: 500.000.000 đồng (Năm trăm triệu đồng). Ban Kế hoạch - Tài chính và Giám đốc Trung tâm R&D chịu trách nhiệm thi hành. Người ký: Phó Tổng Giám đốc Vũ Hải Quang.",
    keywords=[
        "427/QĐ-TNVN",
        "Phó Tổng Giám đốc",
        "Vũ Hải Quang",
        "500.000.000 đồng",
        "dự toán",
        "Trung tâm R&D",
        "Ban Kế hoạch - Tài chính",
    ],
)

CHILD_DUTOAN_1_1 = BenchmarkChunk(
    chunk_id=C_DUTOAN_1_1_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=P_DUTOAN_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 427/QĐ-TNVN", "Điều 1: Phê duyệt dự toán"],
    content_raw="Điều 1. Phê duyệt dự toán kinh phí Hoạt động thông tin khoa học năm 2026 của Đài Tiếng nói Việt Nam: Tổng kinh phí: 500.000.000 đồng (Bằng chữ: Năm trăm triệu đồng). Nguồn kinh phí: Nguồn ngân sách nhà nước năm 2026. Đơn vị thực hiện: Trung tâm Nghiên cứu và ứng dụng Công nghệ Truyền thông (R&D). Người ký: Phó Tổng Giám đốc Vũ Hải Quang.",
    keywords=[
        "427/QĐ-TNVN",
        "Điều 1",
        "500.000.000 đồng",
        "Phó Tổng Giám đốc",
        "Vũ Hải Quang",
        "dự toán",
        "Trung tâm R&D",
    ],
)

CHILD_DUTOAN_1_2 = BenchmarkChunk(
    chunk_id=C_DUTOAN_1_2_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=P_DUTOAN_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 427/QĐ-TNVN", "Điều 2 & Điều 3: Thi hành"],
    content_raw="Điều 2. Giám đốc Trung tâm Nghiên cứu và ứng dụng Công nghệ Truyền thông (R&D) chịu trách nhiệm quản lý, sử dụng và thanh quyết toán kinh phí theo đúng quy định hiện hành.\nĐiều 3. Chánh Văn phòng, Trưởng ban Ban Kế hoạch - Tài chính, Giám đốc Trung tâm R&D và Thủ trưởng các đơn vị có liên quan chịu trách nhiệm thi hành Quyết định này. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Vũ Hải Quang.",
    keywords=[
        "427/QĐ-TNVN",
        "Điều 2",
        "Điều 3",
        "Ban Kế hoạch - Tài chính",
        "Trung tâm R&D",
        "Vũ Hải Quang",
        "thanh quyết toán",
    ],
)

PARENT_DUTOAN_2 = BenchmarkChunk(
    chunk_id=P_DUTOAN_2_ID,
    document_id=DOC_DUTOAN_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Phụ lục dự toán", "Mục I: Phần mềm và bản quyền AI"],
    content_raw="PHỤ LỤC DỰ TOÁN CHI TIẾT HOẠT ĐỘNG THÔNG TIN KHOA HỌC NĂM 2026. Mục I Phần mềm: Plagiarism Checker X 2025 Business: 7.200.000 đ; Second Copy: 7.500.000 đ; Google Workspace có NotebookLM AI: 12.900.000 đ; ChatGPT Business: 53.000.000 đ; Thư viện pháp luật: 4.000.000 đ.",
    keywords=[
        "Phụ lục",
        "Phần mềm",
        "ChatGPT Business",
        "Notebooklm AI",
        "Plagiarism Checker X",
        "Second Copy",
        "Trí tuệ nhân tạo AI",
        "Vũ Hải Quang",
    ],
)

CHILD_DUTOAN_2_1 = BenchmarkChunk(
    chunk_id=C_DUTOAN_2_1_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=P_DUTOAN_2_ID,
    hierarchy_level=1,
    node_type="TABLE_CHILD",
    heading_path=["Phụ lục dự toán", "Bảng chi phí phần mềm"],
    content_raw="| STT | Tên phần mềm / dịch vụ | Số lượng | Đơn vị tính | Thành tiền (VNĐ) |\n|---|---|---|---|---|\n| 1 | Plagiarism Checker X 2025 Business | 1 | Bản | 7.200.000 |\n| 2 | Second Copy | 3 | Bản | 7.500.000 |\n| 3 | Google Workspace (NotebookLM AI) | 1 | Gói/Năm | 12.900.000 |\n| 4 | ChatGPT Business | 5 | Tài khoản/Năm | 53.000.000 |\n| 5 | Thư viện pháp luật tra cứu | 1 | Tài khoản/Năm | 4.000.000 |",
    keywords=[
        "Phần mềm",
        "Plagiarism Checker X",
        "Second Copy",
        "Google Workspace",
        "Notebooklm AI",
        "ChatGPT Business",
        "7.200.000",
        "53.000.000",
        "12.900.000",
        "Thư viện pháp luật",
        "Trí tuệ nhân tạo AI",
    ],
)

CHILD_DUTOAN_2_2 = BenchmarkChunk(
    chunk_id=C_DUTOAN_2_2_ID,
    document_id=DOC_DUTOAN_ID,
    parent_id=P_DUTOAN_2_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Phụ lục dự toán", "Mục II & III: Hội thảo khoa học"],
    content_raw="Mục II: Tổ chức Hội thảo khoa học ứng dụng công nghệ số: 120.000.000 đồng.\nMục III: Sản xuất chuyên mục phát thanh tuyên truyền hoạt động thông tin khoa học công nghệ: 150.000.000 đồng.\nTổng cộng toàn bộ các mục: 500.000.000 đồng.",
    keywords=[
        "Hội thảo khoa học",
        "công nghệ số",
        "tuyên truyền phát thanh",
        "120.000.000",
        "150.000.000",
    ],
)

# 2. Chunks for QĐ 80
PARENT_NHANSU_1 = BenchmarkChunk(
    chunk_id=P_NHANSU_1_ID,
    document_id=DOC_NHANSU_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 80-QĐ/TNVN", "Chấm dứt hợp đồng làm việc"],
    content_raw="Số: 80-QĐ/TNVN ngày 28 tháng 04 năm 2026. QUYẾT ĐỊNH Về việc chấm dứt hợp đồng làm việc đối với viên chức bà Cao Thị Hoa Hương, chuyên viên Đài phát sóng Đối ngoại, Trung tâm Kỹ thuật thuộc Đài Tiếng nói Việt Nam kể từ ngày 01/5/2026, hưởng chế độ bảo hiểm xã hội. Người ký: Phó Tổng Giám đốc Vũ Hải Quang.",
    keywords=[
        "80-QĐ/TNVN",
        "Cao Thị Hoa Hương",
        "chấm dứt hợp đồng",
        "Trung tâm Kỹ thuật",
        "bảo hiểm xã hội",
        "Phó Tổng Giám đốc",
        "Vũ Hải Quang",
    ],
)

CHILD_NHANSU_1_1 = BenchmarkChunk(
    chunk_id=C_NHANSU_1_1_ID,
    document_id=DOC_NHANSU_ID,
    parent_id=P_NHANSU_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 80-QĐ/TNVN", "Căn cứ pháp lý"],
    content_raw="Căn cứ Nghị định số 115/2020/NĐ-CP ngày 25/9/2020 của Chính phủ quy định về tuyển dụng, sử dụng và quản lý viên chức;\nCăn cứ Nghị định số 85/2023/NĐ-CP của Chính phủ sửa đổi Nghị định 115/2020/NĐ-CP;\nTheo đề nghị của Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế tại Tờ trình số 329/TTr-TCCB&HTQT ngày 28/4/2026.",
    keywords=[
        "Nghị định 115/2020/NĐ-CP",
        "Nghị định 85/2023/NĐ-CP",
        "Tờ trình số 329",
        "TCCB&HTQT",
        "Ban Tổ chức cán bộ",
    ],
)

CHILD_NHANSU_1_2 = BenchmarkChunk(
    chunk_id=C_NHANSU_1_2_ID,
    document_id=DOC_NHANSU_ID,
    parent_id=P_NHANSU_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 80-QĐ/TNVN", "Điều 1-3: Chấm dứt HĐ và quyền lợi"],
    content_raw="Điều 1. Chấm dứt hợp đồng làm việc đối với bà Cao Thị Hoa Hương, chuyên viên Đài phát sóng Đối ngoại, Trung tâm Kỹ thuật thuộc Đài Tiếng nói Việt Nam, kể từ ngày 01/5/2026.\nĐiều 2. Bà Cao Thị Hoa Hương được hưởng chế độ bảo hiểm xã hội và chế độ khác theo quy định của pháp luật.\nĐiều 3. Chánh Văn phòng, Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế, Trưởng ban Ban Kế hoạch - Tài chính, Giám đốc Trung tâm Kỹ thuật và bà Cao Thị Hoa Hương chịu trách nhiệm thi hành Quyết định này. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Vũ Hải Quang.",
    keywords=[
        "Điều 1",
        "Điều 2",
        "Điều 3",
        "80-QĐ/TNVN",
        "Cao Thị Hoa Hương",
        "Trung tâm Kỹ thuật",
        "chấm dứt hợp đồng",
        "bảo hiểm xã hội",
        "Ban Kế hoạch - Tài chính",
        "Vũ Hải Quang",
    ],
)

# 3. Chunks for QĐ 587
PARENT_QUYCHE_1 = BenchmarkChunk(
    chunk_id=P_QUYCHE_1_ID,
    document_id=DOC_QUYCHE_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 587/QĐ-TNVN", "Ban hành Quy chế chấm điểm"],
    content_raw="Số: 587 /QĐ-TNVN, Hà Nội, ngày 16 tháng 3 năm 2026. Ban hành Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026. Điều 1 Ban hành kèm theo Quyết định này Quy chế chấm điểm. Điều 2 Quyết định có hiệu lực kể từ ngày ký. Điều 3 Ban Thư ký biên tập, Ban KHTC và Hội đồng Giám khảo thi hành.",
    keywords=[
        "587/QĐ-TNVN",
        "Quy chế chấm điểm",
        "Liên hoan Phát thanh toàn quốc",
        "Quảng Ninh 2026",
        "lần thứ XVII",
        "Điều 1",
        "Điều 2",
        "Điều 3",
    ],
)

CHILD_QUYCHE_1_1 = BenchmarkChunk(
    chunk_id=C_QUYCHE_1_1_ID,
    document_id=DOC_QUYCHE_ID,
    parent_id=P_QUYCHE_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 587/QĐ-TNVN", "Điều 1: Ban hành Quy chế"],
    content_raw="QUYẾT ĐỊNH Về việc ban hành Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026. Căn cứ Nghị định số 46/2025/NĐ-CP; Căn cứ Quyết định số 2214/QĐ-TNVN; Điều 1. Ban hành kèm theo Quyết định này Quy chế chấm điểm Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026.",
    keywords=[
        "587/QĐ-TNVN",
        "Quy chế chấm điểm",
        "Liên hoan Phát thanh toàn quốc",
        "Quảng Ninh 2026",
        "lần thứ XVII",
        "Điều 1",
    ],
)

CHILD_QUYCHE_1_2 = BenchmarkChunk(
    chunk_id=C_QUYCHE_1_2_ID,
    document_id=DOC_QUYCHE_ID,
    parent_id=P_QUYCHE_1_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 587/QĐ-TNVN", "Điều 2 & Điều 3: Hiệu lực thi hành"],
    content_raw="Điều 2. Quyết định này có hiệu lực kể từ ngày ký.\nĐiều 3. Chánh Văn phòng, Trưởng ban Ban Thư ký biên tập, Trưởng ban Ban Tổ chức cán bộ và Hợp tác quốc tế, Trưởng ban Ban Kế hoạch - Tài chính, Thủ trưởng các đơn vị có liên quan và các thành viên Hội đồng Giám khảo Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh 2026 chịu trách nhiệm thi hành Quyết định này.",
    keywords=[
        "Điều 2",
        "Điều 3",
        "Ban Thư ký biên tập",
        "Ban Kế hoạch - Tài chính",
        "Hội đồng Giám khảo",
        "thi hành",
        "hiệu lực",
    ],
)

PARENT_QUYCHE_2 = BenchmarkChunk(
    chunk_id=P_QUYCHE_2_ID,
    document_id=DOC_QUYCHE_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quy chế chấm điểm", "Điều 1: Điều kiện tham dự & Thể loại"],
    content_raw="QUY CHẾ CHẤM ĐIỂM TÁC PHẨM THAM DỰ LIÊN HOAN PHÁT THANH TOÀN QUỐC LẦN THỨ XVII - QUẢNG NINH 2026. Điều 1: ĐIỀU KIỆN THAM DỰ. 1. Tác giả: Phóng viên, biên tập viên, phát thanh viên, kỹ thuật viên thuộc Đài Tiếng nói Việt Nam và các Đài PTTH cả nước. 2. Thể loại: Phóng sự, Phỏng vấn, Câu chuyện truyền thanh, Kịch truyền thanh, Phát thanh trực tiếp.",
    keywords=[
        "Điều kiện tham dự",
        "tác giả",
        "phóng viên",
        "biên tập viên",
        "thể loại",
        "Phóng sự",
        "Phỏng vấn",
        "Kịch truyền thanh",
        "Chương trình phát thanh trực tiếp",
        "Liên hoan Phát thanh 2026",
    ],
)

CHILD_QUYCHE_2_1 = BenchmarkChunk(
    chunk_id=C_QUYCHE_2_1_ID,
    document_id=DOC_QUYCHE_ID,
    parent_id=P_QUYCHE_2_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quy chế chấm điểm", "Điều 1: Tác giả và Thể loại"],
    content_raw="Điều 1: ĐIỀU KIỆN THAM DỰ LIÊN HOAN PHÁT THANH TOÀN QUỐC 2026. 1. Tác giả là phóng viên, biên tập viên, phát thanh viên, kỹ thuật viên thuộc Đài Tiếng nói Việt Nam và các cơ quan truyền thông cả nước. 2. Thể loại tác phẩm tham dự: Phóng sự, Phỏng vấn, Câu chuyện truyền thanh, Kịch truyền thanh, Chương trình phát thanh trực tiếp.",
    keywords=[
        "Điều kiện tham dự",
        "tác giả",
        "phóng viên",
        "biên tập viên",
        "thể loại",
        "Phóng sự",
        "Phỏng vấn",
        "Kịch truyền thanh",
        "Chương trình phát thanh trực tiếp",
        "Liên hoan Phát thanh 2026",
    ],
)

# 4. Chunks for QĐ 87 (Đào tạo AI - Ngô Minh Hiển ký)
PARENT_DAOTAO_87 = BenchmarkChunk(
    chunk_id=P_DAOTAO_87_ID,
    document_id=DOC_DAOTAO_87_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 87/QĐ-TNVN", "Tập huấn Ứng dụng AI trong tòa soạn"],
    content_raw="Số: 87/QĐ-TNVN ngày 29 tháng 4 năm 2026. QUYẾT ĐỊNH về tổ chức Khóa Tập huấn nghiệp vụ: 'Ứng dụng AI trong tòa soạn'. Giao Ban Tổ chức cán bộ và Hợp tác quốc tế đón 01 chuyên gia của Hãng thông tấn Sputnik (Liên bang Nga) tổ chức khóa tập huấn từ 25/5 đến 29/5/2026 tại 58 Quán Sứ, Hà Nội. Người ký: Phó Tổng Giám đốc Ngô Minh Hiển.",
    keywords=[
        "87/QĐ-TNVN",
        "Ứng dụng AI trong tòa soạn",
        "Khóa Tập huấn",
        "Trí tuệ nhân tạo AI",
        "Sputnik",
        "Ngô Minh Hiển",
        "Phó Tổng Giám đốc",
    ],
)

CHILD_DAOTAO_87_1 = BenchmarkChunk(
    chunk_id=C_DAOTAO_87_1_ID,
    document_id=DOC_DAOTAO_87_ID,
    parent_id=P_DAOTAO_87_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 87/QĐ-TNVN", "Điều 1: Tổ chức tập huấn AI"],
    content_raw="Điều 1. Giao Ban Tổ chức cán bộ và Hợp tác quốc tế đón 01 chuyên gia của Hãng thông tấn Sputnik (Liên bang Nga) vào tổ chức Khóa Tập huấn nghiệp vụ: 'Ứng dụng AI trong tòa soạn'. Thời gian: từ ngày 25/5 đến ngày 29/5/2026. Địa điểm: Đài Tiếng nói Việt Nam, 58 Quán Sứ, Hà Nội. K/T TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Ngô Minh Hiển.",
    keywords=[
        "87/QĐ-TNVN",
        "Điều 1",
        "Ứng dụng AI trong tòa soạn",
        "Sputnik",
        "58 Quán Sứ",
        "Ngô Minh Hiển",
        "Phó Tổng Giám đốc",
        "Trí tuệ nhân tạo AI",
    ],
)

# 5. Chunks for QĐ 109 (Tuyển dụng VOV5 - Đỗ Tiến Sỹ ký)
PARENT_NHANSU_109 = BenchmarkChunk(
    chunk_id=P_NHANSU_109_ID,
    document_id=DOC_NHANSU_109_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 109-QĐ/TNVN", "Tuyển dụng viên chức VOV5"],
    content_raw="Số 109 -QĐ/TNVN ngày 05 tháng 5 năm 2026. QUYẾT ĐỊNH về tuyển dụng viên chức. Điều 1 Tuyển dụng bà Hoàng Phương Ly, Thạc sỹ Báo chí và Truyền thông Đại học Sogang Hàn Quốc về làm việc tại Ban Đối ngoại (VOV5) kể từ ngày 01/05/2026. Điều 2 hưởng 85% bậc 2/9 Biên tập viên hạng III. Người ký: Tổng Giám đốc Đỗ Tiến Sỹ.",
    keywords=[
        "109-QĐ/TNVN",
        "Hoàng Phương Ly",
        "Ban Đối ngoại",
        "VOV5",
        "tuyển dụng",
        "Đỗ Tiến Sỹ",
        "Tổng Giám đốc",
    ],
)

CHILD_NHANSU_109_1 = BenchmarkChunk(
    chunk_id=C_NHANSU_109_1_ID,
    document_id=DOC_NHANSU_109_ID,
    parent_id=P_NHANSU_109_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 109-QĐ/TNVN", "Điều 1: Tuyển dụng Hoàng Phương Ly"],
    content_raw="Điều 1. Tuyển dụng bà Hoàng Phương Ly, sinh ngày 23/10/1993, Cử nhân Ngôn ngữ Hàn Quốc, Ngôn ngữ Anh, Đại học Hà Nội; Thạc sỹ Báo chí và Truyền thông, Đại học Sogang Hàn Quốc về làm việc tại Ban Đối ngoại (VOV5) thuộc Đài Tiếng nói Việt Nam, kể từ ngày 01/05/2026. TỔNG GIÁM ĐỐC Đỗ Tiến Sỹ.",
    keywords=[
        "109-QĐ/TNVN",
        "Điều 1",
        "Hoàng Phương Ly",
        "Đại học Sogang",
        "Ban Đối ngoại",
        "VOV5",
        "Đỗ Tiến Sỹ",
        "Tổng Giám đốc",
    ],
)

# 6. Chunks for QĐ 2244 (Phát sóng Cột 5 Quảng Ninh - Vũ Hải Quang ký)
PARENT_PHATSONG_2244 = BenchmarkChunk(
    chunk_id=P_PHATSONG_2244_ID,
    document_id=DOC_PHATSONG_2244_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 2244/QĐ-TNVN", "Phương án phát sóng FM Cột 5 Hạ Long"],
    content_raw="Số: 2244 /QĐ-TNVN ngày 09 tháng 7 năm 2025. QUYẾT ĐỊNH Về việc điều chỉnh phương án phát sóng FM tại trạm phát sóng Cột 5 Hạ Long thuộc Trung tâm Truyền thông tỉnh Quảng Ninh. Giảm công suất từng máy phát FM (VOV1, Tiếng Anh 24/7, VOV5) từ 10 kW xuống 5 kW từ ngày 01/7/2025. Điều chỉnh đơn giá phát sóng xuống 49.100 đ/giờ. Người ký: Phó Tổng Giám đốc Vũ Hải Quang.",
    keywords=[
        "2244/QĐ-TNVN",
        "trạm phát sóng Cột 5",
        "Hạ Long",
        "Quảng Ninh",
        "công suất 5 kW",
        "đơn giá 49.100 đồng",
        "Phát sóng FM",
        "Vũ Hải Quang",
        "Phó Tổng Giám đốc",
    ],
)

CHILD_PHATSONG_2244_1 = BenchmarkChunk(
    chunk_id=C_PHATSONG_2244_1_ID,
    document_id=DOC_PHATSONG_2244_ID,
    parent_id=P_PHATSONG_2244_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 2244/QĐ-TNVN", "Điều 1: Điều chỉnh công suất và đơn giá"],
    content_raw="Điều 1. Điều chỉnh công suất: Giảm công suất của từng máy phát FM (VOV1, Tiếng Anh 24/7, VOV5) từ 10 kW xuống 5 kW. Thời gian thực hiện: Từ ngày 01 tháng 7 năm 2025. Điều 2. Điều chỉnh đơn giá phát sóng: Từ 68.200 đồng/giờ xuống còn 49.100 đồng/giờ cho mỗi máy công suất 5 kW. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Vũ Hải Quang.",
    keywords=[
        "2244/QĐ-TNVN",
        "Điều 1",
        "Điều 2",
        "trạm phát sóng Cột 5",
        "Hạ Long",
        "Quảng Ninh",
        "VOV1",
        "VOV5",
        "Vũ Hải Quang",
        "Phó Tổng Giám đốc",
    ],
)

# 7. Chunks for QĐ 72 (Phát sóng VOV Giao thông Cột 5 Quảng Ninh - Vũ Hải Quang ký)
PARENT_PHATSONG_72 = BenchmarkChunk(
    chunk_id=P_PHATSONG_72_ID,
    document_id=DOC_PHATSONG_72_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 72/QĐ-TNVN", "Phát sóng FM VOV Giao thông Duyên Hải"],
    content_raw="Số: 72 /QĐ-TNVN ngày 15 tháng 01 năm 2026. QUYẾT ĐỊNH Về việc phát sóng FM Kênh VOV Giao thông Duyên Hải tại Trạm phát sóng Cột 5 phường Hạ Long, Quảng Ninh. Tần số 91,5 MHz, công suất 10 kW, thời lượng 18 giờ/ngày (06h00 - 24h00), thực hiện từ ngày 01/02/2026. Người ký: Phó Tổng Giám đốc Vũ Hải Quang.",
    keywords=[
        "72/QĐ-TNVN",
        "trạm phát sóng Cột 5",
        "Hạ Long",
        "Quảng Ninh",
        "VOV Giao thông Duyên Hải",
        "91,5 MHz",
        "công suất 10 kW",
        "Vũ Hải Quang",
        "Phó Tổng Giám đốc",
    ],
)

CHILD_PHATSONG_72_1 = BenchmarkChunk(
    chunk_id=C_PHATSONG_72_1_ID,
    document_id=DOC_PHATSONG_72_ID,
    parent_id=P_PHATSONG_72_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 72/QĐ-TNVN", "Điều 1: Thông số phát sóng VOV Giao thông"],
    content_raw="Điều 1. Phát sóng FM Kênh VOV Giao thông Duyên Hải tại Trạm phát sóng Cột 5, phường Hạ Long, tỉnh Quảng Ninh, với các thông số: Tần số 91,5 MHz; Công suất phát sóng 10 kW; Thời lượng 18 giờ/ngày (06h00 đến 24h00). Thực hiện từ ngày 01 tháng 02 năm 2026. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Vũ Hải Quang.",
    keywords=[
        "72/QĐ-TNVN",
        "Điều 1",
        "trạm phát sóng Cột 5",
        "Hạ Long",
        "Quảng Ninh",
        "VOV Giao thông Duyên Hải",
        "91,5 MHz",
        "Vũ Hải Quang",
        "Phó Tổng Giám đốc",
    ],
)

# 8. Chunks for QĐ 1119 (Bằng khen Liên hoan PT Quảng Ninh - Đỗ Tiến Sỹ ký)
PARENT_THIDUA_1119 = BenchmarkChunk(
    chunk_id=P_THIDUA_1119_ID,
    document_id=DOC_THIDUA_1119_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 1119/QĐ-TNVN", "Tặng Bằng khen Liên hoan Phát thanh 2026"],
    content_raw="Số: 1119/QĐ-TNVN ngày 13 tháng 04 năm 2026. QUYẾT ĐỊNH Về việc tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam cho các tập thể xuất sắc trong Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh, năm 2026: Sở VHTTDL Quảng Ninh, Báo và PTTH Quảng Ninh, Trung tâm QC&DVTT, Ban Thư ký biên tập... TỔNG GIÁM ĐỐC Đỗ Tiến Sỹ.",
    keywords=[
        "1119/QĐ-TNVN",
        "Bằng khen",
        "Liên hoan Phát thanh toàn quốc",
        "lần thứ XVII",
        "Quảng Ninh 2026",
        "Đỗ Tiến Sỹ",
        "Tổng Giám đốc",
    ],
)

CHILD_THIDUA_1119_1 = BenchmarkChunk(
    chunk_id=C_THIDUA_1119_1_ID,
    document_id=DOC_THIDUA_1119_ID,
    parent_id=P_THIDUA_1119_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 1119/QĐ-TNVN", "Điều 1: Danh sách tập thể khen thưởng"],
    content_raw="Điều 1. Tặng Bằng khen của Tổng Giám đốc Đài Tiếng nói Việt Nam cho các tập thể: Sở Văn hóa, Thể thao và Du lịch tỉnh Quảng Ninh; Báo và phát thanh, truyền hình Quảng Ninh; Ban Thư ký biên tập; Trung tâm Quảng cáo... Đã có thành tích xuất sắc trong Liên hoan Phát thanh toàn quốc lần thứ XVII - Quảng Ninh, năm 2026. TỔNG GIÁM ĐỐC Đỗ Tiến Sỹ.",
    keywords=[
        "1119/QĐ-TNVN",
        "Điều 1",
        "Bằng khen",
        "Liên hoan Phát thanh toàn quốc",
        "Quảng Ninh 2026",
        "Đỗ Tiến Sỹ",
        "Tổng Giám đốc",
    ],
)

# 9. Chunks for QĐ 862 (Thâm niên vượt khung - Vũ Hải Quang ký)
PARENT_NHANSU_862 = BenchmarkChunk(
    chunk_id=P_NHANSU_862_ID,
    document_id=DOC_NHANSU_862_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 862/QĐ-TNVN", "Chế độ thâm niên vượt khung 2026"],
    content_raw="Số: 862 /QĐ-TNVN ngày 31 tháng 3 năm 2026. QUYẾT ĐỊNH Về việc thực hiện chế độ thâm niên vượt khung năm 2026 đối với ông Dương Văn Đoàn, Phó Trưởng phòng thường trực Trường Cao đẳng Phát thanh - Truyền hình I. Hệ số cũ: 4,98 + VK 10%; Hệ số mới: 4,98 + VK 11% kể từ ngày 01/01/2026. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Vũ Hải Quang.",
    keywords=[
        "862/QĐ-TNVN",
        "thâm niên vượt khung",
        "Dương Văn Đoàn",
        "Cao đẳng Phát thanh - Truyền hình I",
        "VK 11%",
        "Vũ Hải Quang",
        "Phó Tổng Giám đốc",
    ],
)

CHILD_NHANSU_862_1 = BenchmarkChunk(
    chunk_id=C_NHANSU_862_1_ID,
    document_id=DOC_NHANSU_862_ID,
    parent_id=P_NHANSU_862_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 862/QĐ-TNVN", "Điều 1: Phụ cấp thâm niên ông Dương Văn Đoàn"],
    content_raw="Điều 1. Thực hiện chế độ phụ cấp thâm niên vượt khung năm 2026 đối với ông Dương Văn Đoàn, Phó Trưởng phòng thường trực Trường Cao đẳng Phát thanh - Truyền hình I thuộc Đài Tiếng nói Việt Nam. Hệ số cũ: 4,98 + VK 10%. Hệ số mới: 4,98 + VK 11% kể từ ngày 01/01/2026. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Vũ Hải Quang.",
    keywords=[
        "862/QĐ-TNVN",
        "Điều 1",
        "thâm niên vượt khung",
        "Dương Văn Đoàn",
        "VK 11%",
        "Vũ Hải Quang",
        "Phó Tổng Giám đốc",
    ],
)

# 10. Chunks for Chỉ thị 1838 (EVFTA - Ngô Minh Hiển ký)
PARENT_CHITHI_1838 = BenchmarkChunk(
    chunk_id=P_CHITHI_1838_ID,
    document_id=DOC_CHITHI_1838_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Chỉ thị 1838/CT-TNVN", "Tổ chức diễn đàn trực tuyến EVFTA"],
    content_raw="Số: 1838/CT-TNVN ngày 23 tháng 7 năm 2020. CHỈ THỊ Về việc tổ chức Diễn đàn trực tuyến Hiệp định thương mại tự do Việt Nam - Châu Âu (EVFTA). Phân công Ban Đối ngoại VOV5 chủ trì, Trung tâm R&D kết nối trực tuyến, Đài THKTS VTC chuẩn bị hội trường tại 23 Lạc Trung, Hà Nội. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Ngô Minh Hiển.",
    keywords=[
        "1838/CT-TNVN",
        "Chỉ thị",
        "EVFTA",
        "Ban Đối ngoại",
        "VOV5",
        "Trung tâm R&D",
        "VTC",
        "Ngô Minh Hiển",
        "Phó Tổng Giám đốc",
    ],
)

CHILD_CHITHI_1838_1 = BenchmarkChunk(
    chunk_id=C_CHITHI_1838_1_ID,
    document_id=DOC_CHITHI_1838_ID,
    parent_id=P_CHITHI_1838_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Chỉ thị 1838/CT-TNVN", "Phân công nhiệm vụ các đơn vị"],
    content_raw="I. PHÂN CÔNG NHIỆM VỤ: 1. Ban Đối ngoại (VOV5) là đơn vị đầu mối chủ trì, phối hợp các đơn vị liên quan tổ chức diễn đàn trực tuyến EVFTA. 2. Trung tâm R&D tổ chức kết nối các điểm cầu trực tuyến giữa Việt Nam và châu Âu. 3. Đài Truyền hình kỹ thuật số VTC chuẩn bị hội trường tại 23 Lạc Trung. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Ngô Minh Hiển.",
    keywords=[
        "1838/CT-TNVN",
        "EVFTA",
        "VOV5",
        "Trung tâm R&D",
        "VTC",
        "Ngô Minh Hiển",
        "Phó Tổng Giám đốc",
    ],
)

# 11. Chunks for QĐ 50 (Bồi dưỡng chuyên viên - Phạm Mạnh Hùng ký)
PARENT_DAOTAO_50 = BenchmarkChunk(
    chunk_id=P_DAOTAO_50_ID,
    document_id=DOC_DAOTAO_50_ID,
    hierarchy_level=0,
    node_type="section",
    heading_path=["Quyết định 50/QĐ-TNVN", "Cử viên chức bồi dưỡng chuyên viên"],
    content_raw="Số: 50/QĐ-TNVN ngày 23 tháng 4 năm 2026. QUYẾT ĐỊNH về việc cử viên chức tham gia các lớp Bồi dưỡng ngạch chuyên viên và chuyên viên chính năm 2026. Cử các viên chức Đài TNVN tham gia học tại Trường Cán bộ quản lý VHTTDL. Đài TNVN hỗ trợ 1.000.000 đ/học viên. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Phạm Mạnh Hùng.",
    keywords=[
        "50/QĐ-TNVN",
        "Bồi dưỡng ngạch chuyên viên",
        "chuyên viên chính",
        "Trường Cán bộ quản lý VHTTDL",
        "1.000.000đ",
        "Phạm Mạnh Hùng",
        "Phó Tổng Giám đốc",
    ],
)

CHILD_DAOTAO_50_1 = BenchmarkChunk(
    chunk_id=C_DAOTAO_50_1_ID,
    document_id=DOC_DAOTAO_50_ID,
    parent_id=P_DAOTAO_50_ID,
    hierarchy_level=1,
    node_type="text",
    heading_path=["Quyết định 50/QĐ-TNVN", "Điều 1: Cử viên chức học lớp chuyên viên"],
    content_raw="Điều 1. Cử các viên chức của Đài Tiếng nói Việt Nam tham gia học các lớp Bồi dưỡng chức danh nghề nghiệp: 1. Lớp Bồi dưỡng ngạch Chuyên viên, thời gian 02 tháng, trực tuyến, hỗ trợ 1.000.000 đ/học viên. 2. Lớp Bồi dưỡng ngạch Chuyên viên chính, thời gian 02 tháng. KT. TỔNG GIÁM ĐỐC - PHÓ TỔNG GIÁM ĐỐC Phạm Mạnh Hùng.",
    keywords=[
        "50/QĐ-TNVN",
        "Điều 1",
        "Chuyên viên",
        "Chuyên viên chính",
        "Phạm Mạnh Hùng",
        "Phó Tổng Giám đốc",
        "bồi dưỡng",
    ],
)


# Aliases for backwards compatibility with earlier mocks
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


# ---------------------------------------------------------------------------
# Full Real Corpus Assembly
# ---------------------------------------------------------------------------

BENCHMARK_CORPUS = BenchmarkCorpus(
    documents=[
        DOC_DUTOAN_427,
        DOC_NHANSU_80,
        DOC_QUYCHE_587,
        DOC_DAOTAO_87,
        DOC_NHANSU_109,
        DOC_PHATSONG_2244,
        DOC_PHATSONG_72,
        DOC_THIDUA_1119,
        DOC_NHANSU_862,
        DOC_CHITHI_1838,
        DOC_DAOTAO_50,
    ],
    chunks=[
        # QĐ 427
        PARENT_DUTOAN_1,
        CHILD_DUTOAN_1_1,
        CHILD_DUTOAN_1_2,
        PARENT_DUTOAN_2,
        CHILD_DUTOAN_2_1,
        CHILD_DUTOAN_2_2,
        # QĐ 80
        PARENT_NHANSU_1,
        CHILD_NHANSU_1_1,
        CHILD_NHANSU_1_2,
        # QĐ 587
        PARENT_QUYCHE_1,
        CHILD_QUYCHE_1_1,
        CHILD_QUYCHE_1_2,
        PARENT_QUYCHE_2,
        CHILD_QUYCHE_2_1,
        # QĐ 87
        PARENT_DAOTAO_87,
        CHILD_DAOTAO_87_1,
        # QĐ 109
        PARENT_NHANSU_109,
        CHILD_NHANSU_109_1,
        # QĐ 2244
        PARENT_PHATSONG_2244,
        CHILD_PHATSONG_2244_1,
        # QĐ 72
        PARENT_PHATSONG_72,
        CHILD_PHATSONG_72_1,
        # QĐ 1119
        PARENT_THIDUA_1119,
        CHILD_THIDUA_1119_1,
        # QĐ 862
        PARENT_NHANSU_862,
        CHILD_NHANSU_862_1,
        # CT 1838
        PARENT_CHITHI_1838,
        CHILD_CHITHI_1838_1,
        # QĐ 50
        PARENT_DAOTAO_50,
        CHILD_DAOTAO_50_1,
    ],
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


# ---------------------------------------------------------------------------
# Public Dataset Accessors
# ---------------------------------------------------------------------------


def get_benchmark_corpus() -> BenchmarkCorpus:
    """Return the normative benchmark corpus."""
    return BENCHMARK_CORPUS


def get_benchmark_queries() -> list[BenchmarkQuery]:
    """Return all benchmark queries."""
    return list(BENCHMARK_QUERIES)


def get_queries_by_category(category: BenchmarkQueryCategory) -> list[BenchmarkQuery]:
    """Filter benchmark queries by category."""
    return [q for q in BENCHMARK_QUERIES if q.category == category]


def get_query_by_id(query_id: str) -> BenchmarkQuery | None:
    """Look up a benchmark query by ID."""
    for q in BENCHMARK_QUERIES:
        if q.query_id == query_id:
            return q
    return None
