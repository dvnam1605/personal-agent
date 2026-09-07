"""Batch ingestion script: Ingests real Vietnamese administrative documents into PostgreSQL + pgvector.

Reads all markdown files in data/QuyetDinh, parses them, extracts administrative metadata,
chunks them into parent/child hierarchy, computes embeddings, and persists them into the database.

Run:
    uv run python scripts/ingest_corpus.py
"""

from __future__ import annotations

import asyncio
import hashlib
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
from app.domain.models.ingestion import IngestionStatus  # noqa: E402
from app.infrastructure.db.ingestion_repository import (  # noqa: E402
    SqlAlchemyIngestionRepository,
    SqlAlchemyUnitOfWork,
)
from app.infrastructure.db.session import get_engine, get_session_factory  # noqa: E402
from app.services.ingestion.embedding import (  # noqa: E402
    EmbeddingContract,
    LocalEmbeddingService,
    TransformersPoolingBackend,
)
from app.services.ingestion.jobs import SqlAlchemyIngestionJobStore  # noqa: E402
from app.services.ingestion.orchestrator import IngestionOrchestrator  # noqa: E402
from app.services.ingestion.source import build_local_source_document  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ingest_corpus")


async def main() -> None:
    data_dir = REPO_ROOT / "data" / "QuyetDinh"
    if not data_dir.exists():
        logger.error("Data directory not found: %s", data_dir)
        sys.exit(1)

    md_files = sorted(data_dir.rglob("*.md"))
    logger.info("Found %d markdown files in %s", len(md_files), data_dir)

    # 1. Ensure embedding model snapshot is downloaded and ready
    model_name = settings.embedding.model
    logger.info("Preparing embedding backend for model: %s", model_name)
    try:
        model_path = snapshot_download(model_name, local_files_only=False)
        logger.info("Embedding model loaded from snapshot: %s", model_path)
    except Exception as exc:
        logger.error("Failed to prepare embedding model: %s", exc)
        sys.exit(1)

    # 2. Build local embedding service with CLS pooling for Vietnamese embedding
    backend = TransformersPoolingBackend(model_path, max_length=512)
    logger.info("Loading PyTorch transformer weights into memory...")
    backend._ensure_loaded()
    logger.info("Embedding backend loaded and ready!")

    contract = EmbeddingContract(model_name=model_name, dimensions=settings.embedding.dimensions)
    embedding_service = LocalEmbeddingService(
        backend=backend,
        contract=contract,
        batch_size=settings.embedding.batch_size,
    )

    # 3. Build database orchestrator
    session_factory = get_session_factory()
    repository = SqlAlchemyIngestionRepository(session_factory)
    transaction = SqlAlchemyUnitOfWork(session_factory)
    job_service = SqlAlchemyIngestionJobStore(session_factory)
    orchestrator = IngestionOrchestrator(
        repository=repository,
        transaction=transaction,
        embedding=embedding_service,
        heartbeat_callback=job_service.heartbeat,
    )

    # 4. Ingest each file
    total_start = time.perf_counter()
    success_count = 0
    fail_count = 0
    total_parents = 0
    total_children = 0

    print("\n" + "=" * 80)
    print("STARTING BATCH INGESTION INTO POSTGRESQL + PGVECTOR")
    print("=" * 80 + "\n")

    for idx, file_path in enumerate(md_files, start=1):
        rel_path = file_path.relative_to(data_dir)
        file_bytes = file_path.read_bytes()

        # Deterministic unique source_id from path
        source_id = "src-" + hashlib.sha256(str(rel_path).encode("utf-8")).hexdigest()[:16]
        source = build_local_source_document(
            file_path,
            source_type="preparsed_markdown",
            source_id=source_id,
        )

        try:
            obs = await orchestrator.ingest_source(source, file_bytes)
            if obs.status in (IngestionStatus.COMPLETED, IngestionStatus.SKIPPED):
                success_count += 1
                total_parents += obs.parent_count
                total_children += obs.child_count
                total_ms = int(
                    (
                        obs.parse_duration_seconds
                        + obs.parent_build_duration_seconds
                        + obs.child_build_duration_seconds
                        + obs.embedding_duration_seconds
                        + obs.persist_duration_seconds
                    )
                    * 1000
                )
                doc_id_str = obs.document_id[:16] if obs.document_id else "N/A"
                print(
                    f"[{idx:02d}/{len(md_files)}] {obs.status.value}: {file_path.name[:35]:35} | "
                    f"DocID: {doc_id_str}... | "
                    f"Parents: {obs.parent_count:2d} | Children: {obs.child_count:2d} | "
                    f"Time: {total_ms:5d}ms",
                    flush=True,
                )
            else:
                fail_count += 1
                print(
                    f"[{idx:02d}/{len(md_files)}] FAILED: {file_path.name} | Reason: {obs.failure_reason}",
                    flush=True,
                )
        except Exception as exc:
            fail_count += 1
            logger.exception("Error ingesting %s: %s", file_path.name, exc)

    total_duration = time.perf_counter() - total_start
    print("\n" + "=" * 80)
    print("BATCH INGESTION SUMMARY")
    print("=" * 80)
    print(f"Total files processed : {len(md_files)}")
    print(f"Successfully ingested : {success_count}")
    print(f"Failed                : {fail_count}")
    print(f"Total Parent chunks   : {total_parents}")
    print(f"Total Child chunks    : {total_children}")
    print(
        f"Total time elapsed    : {total_duration:.2f}s (avg {total_duration / len(md_files):.2f}s/doc)"
    )

    # 5. Query PostgreSQL to verify database contents
    async with session_factory() as session:
        res_docs = await session.execute(
            text("SELECT count(*) FROM documents WHERE is_active = true")
        )
        res_chunks = await session.execute(text("SELECT count(*) FROM document_chunks"))
        res_vectors = await session.execute(
            text("SELECT count(*) FROM document_chunks WHERE embedding IS NOT NULL")
        )
        print("\n--- DATABASE VERIFICATION ---")
        print(f"Active Documents in DB : {res_docs.scalar()}")
        print(f"Total Chunks in DB     : {res_chunks.scalar()}")
        print(f"Chunks with pgvector   : {res_vectors.scalar()}")
        print("=" * 80 + "\n")

    # Dispose engine
    engine = get_engine()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
