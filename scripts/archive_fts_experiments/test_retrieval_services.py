import asyncio
import logging
from uuid import UUID, uuid4

from app.services.ingestion.embedding import LocalEmbeddingService, TransformersPoolingBackend, EmbeddingContract
from app.services.retrieval import DenseRetrievalService, SparseRetrievalService, HybridRetrievalService, RetrievalQuery

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mock embedding backend for testing (since we don't want to load the actual model in this test)
class MockEmbeddingBackend:
    def __init__(self, dimension=1024):
        self.dimension = dimension

    def embed(self, texts):
        # Return random vectors of the correct dimension
        import random
        return [[random.uniform(-1, 1) for _ in range(self.dimension)] for _ in texts]

async def test_retrieval_services():
    logger.info("Testing retrieval services...")

    # Create a mock embedding service
    mock_backend = MockEmbeddingBackend(dimension=1024)
    contract = EmbeddingContract(model_name="mock-model", dimensions=1024)
    embedding_service = LocalEmbeddingService(
        backend=mock_backend,
        contract=contract,
        batch_size=32,
        max_attempts=2
    )

    # Create retrieval services
    dense_service = DenseRetrievalService(embedding_service=embedding_service)
    sparse_service = SparseRetrievalService()
    hybrid_service = HybridRetrievalService(dense_service=dense_service, sparse_service=sparse_service)

    # Create a test query
    query = RetrievalQuery(
        mode='CORPUS_SEARCH',
        query_text='luật đất đai',  # Vietnamese query without accent (should work with vietnamese_simple)
        user_id=None,  # No specific user, should match public documents
        limit=5
    )

    try:
        # Test dense retrieval (will likely return empty results since we have no embeddings in DB)
        logger.info("Testing dense retrieval...")
        dense_results = await dense_service.retrieve(query)
        logger.info(f"Dense retrieval returned {len(dense_results)} results")

        # Test sparse retrieval
        logger.info("Testing sparse retrieval...")
        sparse_results = await sparse_service.retrieve(query)
        logger.info(f"Sparse retrieval returned {len(sparse_results)} results")

        # Test hybrid retrieval
        logger.info("Testing hybrid retrieval...")
        hybrid_results = await hybrid_service.retrieve(query)
        logger.info(f"Hybrid retrieval returned {len(hybrid_results)} results")

        # Print some details if we got results
        if sparse_results:
            logger.info("Sample sparse results:")
            for i, result in enumerate(sparse_results[:3]):
                logger.info(f"  {i+1}. ID: {result.id}, Score: {result.score:.4f}")

        if hybrid_results:
            logger.info("Sample hybrid results:")
            for i, result in enumerate(hybrid_results[:3]):
                logger.info(f"  {i+1}. ID: {result.id}, Score: {result.score:.4f}")

        logger.info("Retrieval services test completed successfully!")
        return True

    except Exception as e:
        logger.error(f"Error during retrieval services test: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = asyncio.run(test_retrieval_services())
    if success:
        print("\n✅ All tests passed!")
    else:
        print("\n❌ Some tests failed!")
        exit(1)