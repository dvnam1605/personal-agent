"""The real documents (from data/QuyetDinh) that make up the benchmark corpus."""

from __future__ import annotations

from tests.fixtures.retrieval_benchmark.ids import (
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
)
from tests.fixtures.retrieval_benchmark_types import (
    BenchmarkDocument,
)

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
