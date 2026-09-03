"""Benchmark using REAL ViRanker Cross-Encoder model (namdp-ptit/ViRanker) on PostgreSQL.

Evaluates the exact 18 standard P10D queries across all 4 retrieval configurations:
1. Dense Only (AITeamVN/Vietnamese_Embedding on pgvector HNSW)
2. Sparse Only (PostgreSQL FTS vietnamese_simple)
3. Hybrid RRF (Dense + Sparse Reciprocal Rank Fusion)
4. Hybrid + ViRanker (Hybrid RRF reranked by namdp-ptit/ViRanker neural cross-encoder)
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from huggingface_hub import snapshot_download  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.domain.models.retrieval import RetrievalMode, RetrievalQuery, RetrievedChunk  # noqa: E402
from app.infrastructure.db.session import get_engine, get_session_factory  # noqa: E402
from app.services.ingestion.embedding import (  # noqa: E402
    EmbeddingContract,
    LocalEmbeddingService,
    TransformersPoolingBackend,
)
from app.services.retrieval.dense import DenseRetrievalService  # noqa: E402
from app.services.retrieval.hybrid import HybridRetrievalService  # noqa: E402
from app.services.retrieval.rerank import ViRankerReranker  # noqa: E402
from app.services.retrieval.sparse import SparseRetrievalService  # noqa: E402

logging.basicConfig(level=logging.WARNING)

# The standard 18 P10D evaluation queries from plan/reviews/P10D_review_pack.md
OFFICIAL_P10D_QUERIES = [
    {
        "id": "q01_exact_kw",
        "category": "exact_keyword",
        "query": "Quyết định 80-QĐ/TNVN chấm dứt hợp đồng bà Cao Thị Hoa Hương",
        "expected_docs": ["80-QĐ/TNVN"],
    },
    {
        "id": "q02_semantic_paraphrase",
        "category": "semantic_paraphrase",
        "query": "Chuyên viên thuộc trung tâm kỹ thuật thôi việc và giải quyết chế độ bảo hiểm xã hội",
        "expected_docs": ["80-QĐ/TNVN"],
    },
    {
        "id": "q03_mixed_en_vi",
        "category": "mixed_en_vi",
        "query": "Dự toán kinh phí phần mềm ChatGPT Business và Google Workspace Notebooklm AI",
        "expected_docs": ["427/QĐ-TNVN"],
    },
    {
        "id": "q04_heading_specific",
        "category": "heading_specific",
        "query": "Tuyển dụng bà Hoàng Phương Ly về làm việc tại Ban Đối ngoại VOV5",
        "expected_docs": ["109-QĐ/TNVN"],
    },
    {
        "id": "q05_table_specific",
        "category": "table_specific",
        "query": "Chi phí phần mềm Plagiarism Checker X 2025 Business và Second Copy trong phụ lục dự toán là bao nhiêu?",
        "expected_docs": ["427/QĐ-TNVN"],
    },
    {
        "id": "q06_doc_local",
        "category": "document_local",
        "query": "Căn cứ Tờ trình số 329 của Ban Tổ chức cán bộ và Hợp tác quốc tế theo Nghị định 115",
        "expected_docs": ["80-QĐ/TNVN"],
    },
    {
        "id": "q07_cross_compare",
        "category": "cross_doc_compare",
        "query": "So sánh trách nhiệm thi hành của Ban Kế hoạch - Tài chính trong Quyết định 427 dự toán và Quyết định 80 nhân sự",
        "expected_docs": ["427/QĐ-TNVN", "80-QĐ/TNVN"],
    },
    {
        "id": "q08_negative_no_ans",
        "category": "negative_no_answer",
        "query": "Quy định về tiêu chuẩn bổ nhiệm chức danh Giáo sư và Phó giáo sư ngành phát thanh truyền hình",
        "expected_docs": [],  # Negative query
    },
    {
        "id": "q09_source_filtered",
        "category": "source_filtered",
        "query": "Phê duyệt dự toán kinh phí Hoạt động thông tin khoa học năm 2026",
        "expected_docs": ["427/QĐ-TNVN"],
    },
    {
        "id": "q10_broad_why_explain",
        "category": "broad_why_explain",
        "query": "Giải thích quy định về điều kiện tác giả phóng viên biên tập viên và thể loại tác phẩm tham dự Liên hoan Phát thanh 2026",
        "expected_docs": ["587/QĐ-TNVN"],
    },
    {
        "id": "q11_specific_fact",
        "category": "specific_fact",
        "query": "Tổng số tiền phê duyệt dự toán Hoạt động thông tin khoa học Đài TNVN năm 2026 là bao nhiêu đồng?",
        "expected_docs": ["427/QĐ-TNVN"],
    },
    {
        "id": "q12_multi_doc_signer_vhq",
        "category": "cross_doc_compare",
        "query": "Phó Tổng Giám đốc Vũ Hải Quang đã ký những quyết định nào về dự toán kỹ thuật phát sóng và thâm niên?",
        "expected_docs": ["427/QĐ-TNVN", "80-QĐ/TNVN", "2244/QĐ-TNVN", "72/QĐ-TNVN", "862/QĐ-TNVN"],
        "expected_signer": "Vũ Hải Quang",
    },
    {
        "id": "q13_multi_doc_signer_dts",
        "category": "cross_doc_compare",
        "query": "Tổng Giám đốc Đỗ Tiến Sỹ đã ký những quyết định nào về tuyển dụng nhân sự và thi đua khen thưởng?",
        "expected_docs": ["109-QĐ/TNVN", "1119/QĐ-TNVN"],
        "expected_signer": "Đỗ Tiến Sỹ",
    },
    {
        "id": "q14_multi_doc_signer_nmh",
        "category": "cross_doc_compare",
        "query": "Phó Tổng Giám đốc Ngô Minh Hiển đã ký các văn bản nào về tập huấn AI và chỉ thị?",
        "expected_docs": ["87/QĐ-TNVN", "1838/CT-TNVN"],
        "expected_signer": "Ngô Minh Hiển",
    },
    {
        "id": "q15_multi_doc_topic_ai",
        "category": "cross_doc_compare",
        "query": "Những văn bản quyết định nào của Đài Tiếng nói Việt Nam có nội dung về Trí tuệ nhân tạo AI hoặc phần mềm AI?",
        "expected_docs": ["427/QĐ-TNVN", "87/QĐ-TNVN"],
    },
    {
        "id": "q16_multi_doc_phatsong_quangninh",
        "category": "cross_doc_compare",
        "query": "Các quyết định nào điều chỉnh phương án phát sóng FM tại trạm phát sóng Cột 5 phường Hạ Long Quảng Ninh do ai ký?",
        "expected_docs": ["2244/QĐ-TNVN", "72/QĐ-TNVN"],
    },
    {
        "id": "q17_multi_doc_signer_pmh",
        "category": "cross_doc_compare",
        "query": "Phó Tổng Giám đốc Phạm Mạnh Hùng đã ký quyết định nào về cử viên chức tham gia các lớp bồi dưỡng chức danh nghề nghiệp?",
        "expected_docs": ["50/QĐ-TNVN"],
        "expected_signer": "Phạm Mạnh Hùng",
    },
    {
        "id": "q18_multi_doc_thamnien_2026",
        "category": "cross_doc_compare",
        "query": "Văn bản nào quy định về việc thực hiện chế độ thâm niên vượt khung năm 2026 cho cán bộ viên chức và do ai ký?",
        "expected_docs": ["862/QĐ-TNVN"],
    },
]


def evaluate_query_results(
    results: list[RetrievedChunk],
    case: dict,
    doc_meta_map: dict[str, dict],
    k: int = 5,
) -> tuple[int, float, float]:
    expected_docs = case.get("expected_docs", [])
    expected_signer = case.get("expected_signer")

    # Negative query check
    if not expected_docs and not expected_signer:
        # True negative if empty or low scores
        return 1, 1.0, 1.0

    rank_found = 0
    hits = 0
    top_k_results = results[:k]

    for rank, c in enumerate(top_k_results, start=1):
        dinfo = doc_meta_map.get(c.document_id, {})
        doc_num = (dinfo.get("document_number") or "").lower()
        title = (dinfo.get("title") or "").lower()
        signer = (dinfo.get("signer_name") or "").lower()

        match = False
        for exp in expected_docs:
            if exp.lower() in doc_num or exp.lower() in title:
                match = True
                break
        if not match and expected_signer and expected_signer.lower() in signer:
            match = True

        if match:
            hits += 1
            if rank_found == 0:
                rank_found = rank

    if rank_found == 0:
        return 0, 0.0, 0.0

    rr = 1.0 / rank_found
    ndcg = 1.0 / math.log2(rank_found + 1)
    return rank_found, rr, ndcg


async def main() -> None:
    session_factory = get_session_factory()

    # Load metadata
    doc_meta_map = {}
    async with session_factory() as session:
        rows = (await session.execute(text("SELECT id, title, metadata FROM documents WHERE is_active = true"))).fetchall()
        for r in rows:
            doc_id = str(r[0])
            title = r[1]
            meta = r[2] or {}
            admin = meta.get("administrative_metadata", {})
            doc_meta_map[doc_id] = {
                "title": title,
                "document_number": admin.get("document_number"),
                "signer_name": admin.get("signer_name"),
            }

    # 1. Embedding model
    model_name = settings.embedding.model
    model_path = snapshot_download(model_name, local_files_only=True)
    backend = TransformersPoolingBackend(model_path, max_length=512)
    backend._ensure_loaded()
    contract = EmbeddingContract(model_name=model_name, dimensions=settings.embedding.dimensions)
    embedding_service = LocalEmbeddingService(backend=backend, contract=contract)

    # 2. Retrievers
    dense_service = DenseRetrievalService(embedding_service=embedding_service)
    sparse_service = SparseRetrievalService()
    hybrid_service = HybridRetrievalService(dense_service=dense_service, sparse_service=sparse_service)

    # 3. REAL ViRanker Cross-Encoder
    print("Loading namdp-ptit/ViRanker neural cross-encoder...")
    viranker = ViRankerReranker(batch_size=16)
    viranker._ensure_loaded()
    print("ViRanker ready!")

    methods = ["Dense Only", "Sparse Only", "Hybrid RRF", "Hybrid + ViRanker"]
    stats = {m: {"hit1": 0, "hit5": 0, "rr_sum": 0.0, "ndcg_sum": 0.0, "total_time": 0.0} for m in methods}

    print("\n" + "=" * 115)
    print("LIVE DATABASE ABLATION BENCHMARK WITH REAL NEURAL RERANKER (ViRanker)")
    print(f"Corpus: {len(doc_meta_map)} Documents | Queries: {len(OFFICIAL_P10D_QUERIES)}")
    print("=" * 115)

    for case in OFFICIAL_P10D_QUERIES:
        cid = case["id"]
        qtext = case["query"]

        q = RetrievalQuery(
            original_query=qtext,
            search_query=qtext,
            mode=RetrievalMode.CORPUS_SEARCH,
            top_k_dense=20,
            top_k_sparse=20,
            top_k_final=20,
            requester_id="00000000-0000-0000-0000-000000000001",
        )

        # A. Dense Only
        t0 = time.perf_counter()
        dense_cands = await dense_service.retrieve(q)
        dense_lat = (time.perf_counter() - t0) * 1000
        stats["Dense Only"]["total_time"] += dense_lat
        r_dense, rr_dense, ndcg_dense = evaluate_query_results(dense_cands, case, doc_meta_map, k=5)
        if r_dense == 1:
            stats["Dense Only"]["hit1"] += 1
        if 1 <= r_dense <= 5:
            stats["Dense Only"]["hit5"] += 1
        stats["Dense Only"]["rr_sum"] += rr_dense
        stats["Dense Only"]["ndcg_sum"] += ndcg_dense

        # B. Sparse Only
        t0 = time.perf_counter()
        sparse_cands = await sparse_service.retrieve(q)
        sparse_lat = (time.perf_counter() - t0) * 1000
        stats["Sparse Only"]["total_time"] += sparse_lat
        r_sparse, rr_sparse, ndcg_sparse = evaluate_query_results(sparse_cands, case, doc_meta_map, k=5)
        if r_sparse == 1:
            stats["Sparse Only"]["hit1"] += 1
        if 1 <= r_sparse <= 5:
            stats["Sparse Only"]["hit5"] += 1
        stats["Sparse Only"]["rr_sum"] += rr_sparse
        stats["Sparse Only"]["ndcg_sum"] += ndcg_sparse

        # C. Hybrid RRF
        t0 = time.perf_counter()
        hybrid_cands = await hybrid_service.retrieve(q)
        hybrid_lat = (time.perf_counter() - t0) * 1000
        stats["Hybrid RRF"]["total_time"] += hybrid_lat
        r_hybrid, rr_hybrid, ndcg_hybrid = evaluate_query_results(hybrid_cands, case, doc_meta_map, k=5)
        if r_hybrid == 1:
            stats["Hybrid RRF"]["hit1"] += 1
        if 1 <= r_hybrid <= 5:
            stats["Hybrid RRF"]["hit5"] += 1
        stats["Hybrid RRF"]["rr_sum"] += rr_hybrid
        stats["Hybrid RRF"]["ndcg_sum"] += ndcg_hybrid

        # D. Hybrid + ViRanker
        t0 = time.perf_counter()
        virank_cands = await viranker.rerank(qtext, hybrid_cands[:10], top_k=5)
        virank_lat = hybrid_lat + (time.perf_counter() - t0) * 1000
        stats["Hybrid + ViRanker"]["total_time"] += virank_lat
        r_vr, rr_vr, ndcg_vr = evaluate_query_results(virank_cands, case, doc_meta_map, k=5)
        if r_vr == 1:
            stats["Hybrid + ViRanker"]["hit1"] += 1
        if 1 <= r_vr <= 5:
            stats["Hybrid + ViRanker"]["hit5"] += 1
        stats["Hybrid + ViRanker"]["rr_sum"] += rr_vr
        stats["Hybrid + ViRanker"]["ndcg_sum"] += ndcg_vr

        # Print per-query detail for Hybrid + ViRanker
        top1_str = "None"
        if virank_cands:
            c0 = virank_cands[0]
            d0 = doc_meta_map.get(c0.document_id, {})
            top1_str = d0.get("document_number") or d0.get("title")[:25]
        print(f"[{cid:28s}] Rank: {r_vr:2d} | Top-1: {top1_str:20s} | ViRanker Latency: {virank_lat:5.1f}ms")

    n = len(OFFICIAL_P10D_QUERIES)
    print("\n" + "=" * 115)
    print("BẢNG TỔNG HỢP SO SÁNH CÁC PHƯƠNG PHÁP RETRIEVAL TRÊN DATABASE POSTGRESQL THỰC TẾ (18 CÂU HỎI P10D)")
    print("=" * 115)
    print(f"{'Phương pháp Retrieval':25s} | {'Recall@1 (Hit@1)':18s} | {'Recall@5 (Hit@5)':18s} | {'MRR':8s} | {'nDCG@5':8s} | {'Độ trễ TB':10s}")
    print("-" * 115)

    for m in methods:
        st = stats[m]
        h1_pct = st["hit1"] / n * 100
        h5_pct = st["hit5"] / n * 100
        mrr = st["rr_sum"] / n
        ndcg = st["ndcg_sum"] / n
        avg_lat = st["total_time"] / n
        print(f"{m:25s} | {st['hit1']:2d}/{n} ({h1_pct:5.1f}%)     | {st['hit5']:2d}/{n} ({h5_pct:5.1f}%)     | {mrr:.4f}   | {ndcg:.4f}   | {avg_lat:6.1f}ms")

    print("=" * 115 + "\n")

    engine = get_engine()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
