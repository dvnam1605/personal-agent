"""Real Database Benchmark: Evaluates retrieval queries directly on live PostgreSQL + pgvector.

Executes Dense (pgvector HNSW cosine search), Sparse (PostgreSQL FTS with vietnamese_simple dictionary),
and Hybrid RRF over all 37 real administrative documents ingested into the database.

Run:
    uv run python scripts/benchmark_live_database.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

# Fix Windows console encoding
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

# 18 Standardized evaluation queries spanning all categories + multi-document tracking
BENCHMARK_QUERIES = [
    # 1. Chi phí / Dự toán
    ("q01_dutoan_chatgpt", "Dự toán chi mua bản quyền ChatGPT Teams 2026 là bao nhiêu?", "427/QĐ-TNVN"),
    ("q02_dutoan_phanmem_ai", "Kinh phí thuê tài khoản NotebookLM Audio Overviews năm 2026", "427/QĐ-TNVN"),
    # 2. Nhân sự / Chấm dứt HĐ
    ("q03_nhansu_cham_dut_hd", "Chấm dứt hợp đồng làm việc của bà Nguyễn Thị Kim Lan", "80-QĐ/TNVN"),
    ("q04_nhansu_ly_do_thoi_viec", "Thời điểm thôi hưởng lương của viên chức Nguyễn Thị Kim Lan", "80-QĐ/TNVN"),
    # 3. Khen thưởng
    ("q05_khenthuong_tapthe", "Khen thưởng danh hiệu Tập thể lao động xuất sắc năm 2025 gồm những đơn vị nào?", "109-QĐ/TNVN"),
    ("q06_khenthuong_coquan_tnvn", "Ban Tổ chức cán bộ và Hợp tác quốc tế được tặng Bằng khen gì năm 2025?", "1119/QĐ-TNVN"),
    # 4. Đào tạo
    ("q07_daotao_ai_toasoan", "Tập huấn ứng dụng AI trong tòa soạn do chuyên gia nào giảng dạy?", "87/QĐ-TNVN"),
    ("q08_daotao_ngach_chuyenvien", "Kế hoạch bồi dưỡng ngạch chuyên viên và tương đương năm 2026", "50/QĐ-TNVN"),
    # 5. Kỹ thuật / Phát sóng
    ("q09_phatsong_quangninh_cot5", "Dự án phát sóng FM tại trạm Cột 5 Quảng Ninh nghiệm thu năm nào?", "2244/QĐ-TNVN"),
    ("q10_phatsong_may_fm", "Bảo dưỡng máy phát sóng FM trạm Cột 5 Quảng Ninh năm 2026", "72/QĐ-TNVN"),
    # 6. Chỉ thị
    ("q11_chithi_evfta", "Chỉ thị về công tác thông tin tuyên truyền Hiệp định EVFTA", "1838/CT-TNVN"),
    # 7. Multi-document & Signer queries (Truy vấn đa văn bản & Người ký)
    ("q12_multi_doc_signer_vhq", "Phó Tổng Giám đốc Vũ Hải Quang ký những văn bản nào?", "Vũ Hải Quang"),
    ("q13_multi_doc_signer_dts", "Tổng Giám đốc Đỗ Tiến Sỹ ký những văn bản nào?", "Đỗ Tiến Sỹ"),
    ("q14_multi_doc_signer_nmh", "Phó Tổng Giám đốc Ngô Minh Hiển ký những văn bản nào?", "Ngô Minh Hiển"),
    ("q15_multi_doc_signer_pmh", "Phó Tổng Giám đốc Phạm Mạnh Hùng ký những văn bản nào?", "Phạm Mạnh Hùng"),
    ("q16_multi_doc_topic_ai", "Các quyết định về ứng dụng trí tuệ nhân tạo và ChatGPT tại VOV", "Trí tuệ nhân tạo"),
    ("q17_multi_doc_phatsong_quangninh", "Các quyết định liên quan đến trạm phát sóng Cột 5 Quảng Ninh", "Cột 5 Quảng Ninh"),
    ("q18_multi_doc_thamnien_2026", "Phụ cấp thâm niên vượt khung cho cán bộ viên chức năm 2026", "862/QĐ-TNVN"),
]


async def main() -> None:
    session_factory = get_session_factory()

    # Verify DB content
    async with session_factory() as session:
        doc_count = (await session.execute(text("SELECT count(*) FROM documents WHERE is_active = true"))).scalar()
        chunk_count = (await session.execute(text("SELECT count(*) FROM document_chunks"))).scalar()
        vec_count = (await session.execute(text("SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL"))).scalar()

    print("\n" + "=" * 95)
    print("LIVE POSTGRESQL + PGVECTOR RETRIEVAL BENCHMARK")
    print(f"Database Corpus: {doc_count} Active Documents | {chunk_count} Chunks | {vec_count} pgvector Embeddings")
    print("=" * 95 + "\n")

    # Load embedding model
    model_name = settings.embedding.model
    model_path = snapshot_download(model_name, local_files_only=True)
    backend = TransformersPoolingBackend(model_path, max_length=512)
    contract = EmbeddingContract(model_name=model_name, dimensions=settings.embedding.dimensions)
    embedding_service = LocalEmbeddingService(backend=backend, contract=contract)

    # Initialize retrievers
    dense_service = DenseRetrievalService(embedding_service=embedding_service)
    sparse_service = SparseRetrievalService()
    hybrid_service = HybridRetrievalService(dense_service=dense_service, sparse_service=sparse_service)

    total_dense_time = 0.0
    total_sparse_time = 0.0
    total_hybrid_time = 0.0

    print(f"{'No':2s} | {'Query Description / Text':45s} | {'Expected Target':18s} | {'Hybrid Top-1 Match':35s} | {'Latency':7s}")
    print("-" * 115)

    for idx, (qid, query_text, target) in enumerate(BENCHMARK_QUERIES, start=1):
        q = RetrievalQuery(
            original_query=query_text,
            search_query=query_text,
            mode=RetrievalMode.CORPUS_SEARCH,
            top_k_dense=20,
            top_k_sparse=20,
            top_k_final=5,
            requester_id="00000000-0000-0000-0000-000000000001",
        )

        t0 = time.perf_counter()
        hybrid_results = await hybrid_service.retrieve(q)
        lat_ms = (time.perf_counter() - t0) * 1000
        total_hybrid_time += lat_ms

        top1_snippet = ""
        hit = False
        if hybrid_results:
            top1 = hybrid_results[0]
            # Match target against content_raw or metadata
            content = top1.content_raw.lower()
            admin_meta = str(top1.metadata).lower()
            if target.lower() in content or target.lower() in admin_meta:
                hit = True
            first_line = top1.content_raw.splitlines()[0].strip()[:35]
            top1_snippet = f"[{'HIT' if hit else 'MISS'}] {first_line}"
        else:
            top1_snippet = "[NO RESULT]"

        print(f"{idx:02d} | {query_text[:45]:45s} | {target:18s} | {top1_snippet:35s} | {lat_ms:5.1f}ms")

    print("-" * 115)
    print(f"Total Hybrid Queries: {len(BENCHMARK_QUERIES)}")
    print(f"Average Live Hybrid Latency: {total_hybrid_time / len(BENCHMARK_QUERIES):.1f}ms (including live query embedding + pgvector HNSW + FTS + RRF)")
    print("=" * 115 + "\n")

    engine = get_engine()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
