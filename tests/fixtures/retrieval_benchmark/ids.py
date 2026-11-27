"""Deterministic UUIDs of the benchmark documents and their parent/child chunks."""

from __future__ import annotations

import uuid

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
