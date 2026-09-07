"""Detailed Analysis of Live PostgreSQL Retrieval Benchmark.

Inspects Top-5 retrieved chunks, joining with documents and administrative metadata
to measure exact Document Hit@1, Document Hit@5, MRR, and category performance.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from huggingface_hub import snapshot_download  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.domain.models.retrieval import RetrievalMode, RetrievalQuery  # noqa: E402
from app.infrastructure.db.session import get_engine, get_session_factory  # noqa: E402
from app.services.ingestion.embedding import (  # noqa: E402
    EmbeddingContract,
    LocalEmbeddingService,
    TransformersPoolingBackend,
)
from app.services.retrieval.dense import DenseRetrievalService  # noqa: E402
from app.services.retrieval.hybrid import HybridRetrievalService  # noqa: E402
from app.services.retrieval.sparse import SparseRetrievalService  # noqa: E402

logging.basicConfig(level=logging.WARNING)

BENCHMARK_CASES = [
    {
        "id": "Q01",
        "category": "Dự toán / Tài chính",
        "query": "Dự toán chi mua bản quyền ChatGPT Teams 2026 là bao nhiêu?",
        "expected_doc_number": "427/QĐ-TNVN",
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "Q02",
        "category": "Dự toán / Tài chính",
        "query": "Kinh phí thuê tài khoản NotebookLM Audio Overviews năm 2026",
        "expected_doc_number": "427/QĐ-TNVN",
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "Q03",
        "category": "Nhân sự / Lao động",
        "query": "Chấm dứt hợp đồng làm việc của bà Nguyễn Thị Kim Lan",
        "expected_doc_number": "80-QĐ/TNVN",
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "Q04",
        "category": "Nhân sự / Lao động",
        "query": "Thời điểm thôi hưởng lương của viên chức Nguyễn Thị Kim Lan",
        "expected_doc_number": "80-QĐ/TNVN",
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "Q05",
        "category": "Khen thưởng",
        "query": "Khen thưởng danh hiệu Tập thể lao động xuất sắc năm 2025 gồm những đơn vị nào?",
        "expected_doc_number": "109-QĐ/TNVN",
        "expected_signer": "Đỗ Tiến Sỹ",
    },
    {
        "id": "Q06",
        "category": "Khen thưởng",
        "query": "Ban Tổ chức cán bộ và Hợp tác quốc tế được tặng Bằng khen gì năm 2025?",
        "expected_doc_number": "1119/QĐ-TNVN",
        "expected_signer": "Đỗ Tiến Sỹ",
    },
    {
        "id": "Q07",
        "category": "Đào tạo",
        "query": "Tập huấn ứng dụng AI trong tòa soạn do chuyên gia nào giảng dạy?",
        "expected_doc_number": "87/QĐ-TNVN",
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "Q08",
        "category": "Đào tạo",
        "query": "Kế hoạch bồi dưỡng ngạch chuyên viên và tương đương năm 2026",
        "expected_doc_number": "50/QĐ-TNVN",
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "Q09",
        "category": "Kỹ thuật / Phát sóng",
        "query": "Dự án phát sóng FM tại trạm Cột 5 Quảng Ninh nghiệm thu năm nào?",
        "expected_doc_number": "2244/QĐ-TNVN",
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "Q10",
        "category": "Kỹ thuật / Phát sóng",
        "query": "Bảo dưỡng máy phát sóng FM trạm Cột 5 Quảng Ninh năm 2026",
        "expected_doc_number": "72/QĐ-TNVN",
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "Q11",
        "category": "Chỉ thị",
        "query": "Chỉ thị về công tác thông tin tuyên truyền Hiệp định EVFTA",
        "expected_doc_number": "1838/CT-TNVN",
        "expected_signer": None,
    },
    {
        "id": "Q12",
        "category": "Truy vấn Người ký",
        "query": "Phó Tổng Giám đốc Vũ Hải Quang ký những văn bản nào?",
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "Q13",
        "category": "Truy vấn Người ký",
        "query": "Tổng Giám đốc Đỗ Tiến Sỹ ký những văn bản nào?",
        "expected_signer": "Đỗ Tiến Sỹ",
    },
    {
        "id": "Q14",
        "category": "Truy vấn Người ký",
        "query": "Phó Tổng Giám đốc Ngô Minh Hiển ký những văn bản nào?",
        "expected_signer": "Ngô Minh Hiển",
    },
    {
        "id": "Q15",
        "category": "Truy vấn Người ký",
        "query": "Phó Tổng Giám đốc Phạm Mạnh Hùng ký những văn bản nào?",
        "expected_signer": "Phạm Mạnh Hùng",
    },
    {
        "id": "Q16",
        "category": "Đa văn bản / Chủ đề",
        "query": "Các quyết định về ứng dụng trí tuệ nhân tạo và ChatGPT tại VOV",
        "expected_doc_number": "427/QĐ-TNVN",
    },
    {
        "id": "Q17",
        "category": "Đa văn bản / Địa bàn",
        "query": "Các quyết định liên quan đến trạm phát sóng Cột 5 Quảng Ninh",
        "expected_doc_number": "2244/QĐ-TNVN",
    },
    {
        "id": "Q18",
        "category": "Chế độ / Tiền lương",
        "query": "Phụ cấp thâm niên vượt khung cho cán bộ viên chức năm 2026",
        "expected_doc_number": "862/QĐ-TNVN",
        "expected_signer": "Vũ Hải Quang",
    },
]


async def main() -> None:
    session_factory = get_session_factory()

    # Load mapping of document_id -> (title, administrative_metadata)
    doc_meta_map: dict[str, dict] = {}
    async with session_factory() as session:
        rows = (
            await session.execute(
                text("SELECT id, title, metadata FROM documents WHERE is_active = true")
            )
        ).fetchall()
        for r in rows:
            doc_id = str(r[0])
            title = r[1]
            meta = r[2] or {}
            admin = meta.get("administrative_metadata", {})
            doc_meta_map[doc_id] = {
                "title": title,
                "document_number": admin.get("document_number"),
                "signer_name": admin.get("signer_name"),
                "promulgation_date": admin.get("promulgation_date"),
                "subject": admin.get("subject"),
            }

    model_name = settings.embedding.model
    model_path = snapshot_download(model_name, local_files_only=True)
    backend = TransformersPoolingBackend(model_path, max_length=512)
    backend._ensure_loaded()
    contract = EmbeddingContract(model_name=model_name, dimensions=settings.embedding.dimensions)
    embedding_service = LocalEmbeddingService(backend=backend, contract=contract)

    dense_service = DenseRetrievalService(embedding_service=embedding_service)
    sparse_service = SparseRetrievalService()
    hybrid_service = HybridRetrievalService(
        dense_service=dense_service, sparse_service=sparse_service
    )

    hit_at_1 = 0
    hit_at_5 = 0
    reciprocal_ranks = []
    latencies = []

    print("\n" + "=" * 115)
    print("DETAILED RETRIEVAL BENCHMARK REPORT (POSTGRESQL + PGVECTOR + ADMINISTRATIVE METADATA)")
    print("=" * 115)

    for case in BENCHMARK_CASES:
        cid = case["id"]
        qtext = case["query"]
        expected_doc = case.get("expected_doc_number")
        expected_signer = case.get("expected_signer")

        q = RetrievalQuery(
            original_query=qtext,
            search_query=qtext,
            mode=RetrievalMode.CORPUS_SEARCH,
            top_k_dense=20,
            top_k_sparse=20,
            top_k_final=5,
            requester_id="00000000-0000-0000-0000-000000000001",
        )

        t0 = time.perf_counter()
        results = await hybrid_service.retrieve(q)
        lat = (time.perf_counter() - t0) * 1000
        latencies.append(lat)

        rank_found = 0
        top1_info = "None"

        for rank, ch in enumerate(results, start=1):
            dinfo = doc_meta_map.get(ch.document_id, {})
            doc_num = dinfo.get("document_number") or ""
            signer = dinfo.get("signer_name") or ""
            title = dinfo.get("title") or ""

            # Check match
            match = False
            if expected_doc and expected_doc.lower() in doc_num.lower():
                match = True
            elif expected_signer and expected_signer.lower() in signer.lower():
                match = True

            if rank == 1:
                top1_info = f"Doc: {doc_num or title[:25]} | Signer: {signer or 'N/A'}"

            if match and rank_found == 0:
                rank_found = rank

        if rank_found == 1:
            hit_at_1 += 1
            hit_at_5 += 1
            reciprocal_ranks.append(1.0)
            status_str = "HIT @ 1"
        elif rank_found > 1:
            hit_at_5 += 1
            reciprocal_ranks.append(1.0 / rank_found)
            status_str = f"HIT @ {rank_found}"
        else:
            reciprocal_ranks.append(0.0)
            status_str = "MISS"

        target_display = expected_doc or expected_signer or "N/A"
        print(
            f"[{cid}] {qtext[:42]:42s} | Target: {target_display:15s} | {status_str:7s} | {top1_info:40s} | {lat:5.1f}ms"
        )

    mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
    p_at_1 = hit_at_1 / len(BENCHMARK_CASES) * 100
    p_at_5 = hit_at_5 / len(BENCHMARK_CASES) * 100
    avg_lat = sum(latencies) / len(latencies)

    print("-" * 115)
    print("OVERALL LIVE DATABASE BENCHMARK METRICS:")
    print(f"  Total Evaluated Queries : {len(BENCHMARK_CASES)}")
    print(f"  Recall / Hit@1 Rate     : {hit_at_1}/{len(BENCHMARK_CASES)} ({p_at_1:.1f}%)")
    print(f"  Recall / Hit@5 Rate     : {hit_at_5}/{len(BENCHMARK_CASES)} ({p_at_5:.1f}%)")
    print(f"  Mean Reciprocal Rank    : {mrr:.3f}")
    print(f"  Average Query Latency   : {avg_lat:.1f}ms")
    print("=" * 115 + "\n")

    engine = get_engine()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
