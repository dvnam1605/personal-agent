"""The assembled benchmark corpus: every document and chunk."""

from __future__ import annotations

from tests.fixtures.retrieval_benchmark.chunks import (
    CHILD_CHITHI_1838_1,
    CHILD_DAOTAO_50_1,
    CHILD_DAOTAO_87_1,
    CHILD_DUTOAN_1_1,
    CHILD_DUTOAN_1_2,
    CHILD_DUTOAN_2_1,
    CHILD_DUTOAN_2_2,
    CHILD_NHANSU_1_1,
    CHILD_NHANSU_1_2,
    CHILD_NHANSU_109_1,
    CHILD_NHANSU_862_1,
    CHILD_PHATSONG_72_1,
    CHILD_PHATSONG_2244_1,
    CHILD_QUYCHE_1_1,
    CHILD_QUYCHE_1_2,
    CHILD_QUYCHE_2_1,
    CHILD_THIDUA_1119_1,
    PARENT_CHITHI_1838,
    PARENT_DAOTAO_50,
    PARENT_DAOTAO_87,
    PARENT_DUTOAN_1,
    PARENT_DUTOAN_2,
    PARENT_NHANSU_1,
    PARENT_NHANSU_109,
    PARENT_NHANSU_862,
    PARENT_PHATSONG_72,
    PARENT_PHATSONG_2244,
    PARENT_QUYCHE_1,
    PARENT_QUYCHE_2,
    PARENT_THIDUA_1119,
)
from tests.fixtures.retrieval_benchmark.documents import (
    DOC_CHITHI_1838,
    DOC_DAOTAO_50,
    DOC_DAOTAO_87,
    DOC_DUTOAN_427,
    DOC_NHANSU_80,
    DOC_NHANSU_109,
    DOC_NHANSU_862,
    DOC_PHATSONG_72,
    DOC_PHATSONG_2244,
    DOC_QUYCHE_587,
    DOC_THIDUA_1119,
)
from tests.fixtures.retrieval_benchmark_types import (
    BenchmarkCorpus,
)

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
