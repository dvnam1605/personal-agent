"""Parent and child chunks of the benchmark documents, plus legacy aliases."""

from __future__ import annotations

from tests.fixtures.retrieval_benchmark.ids import (
    C_CHITHI_1838_1_ID,
    C_DAOTAO_50_1_ID,
    C_DAOTAO_87_1_ID,
    C_DUTOAN_1_1_ID,
    C_DUTOAN_1_2_ID,
    C_DUTOAN_2_1_ID,
    C_DUTOAN_2_2_ID,
    C_NHANSU_1_1_ID,
    C_NHANSU_1_2_ID,
    C_NHANSU_109_1_ID,
    C_NHANSU_862_1_ID,
    C_PHATSONG_72_1_ID,
    C_PHATSONG_2244_1_ID,
    C_QUYCHE_1_1_ID,
    C_QUYCHE_1_2_ID,
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
    P_QUYCHE_1_ID,
    P_QUYCHE_2_ID,
    P_THIDUA_1119_ID,
)
from tests.fixtures.retrieval_benchmark_types import (
    BenchmarkChunk,
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
