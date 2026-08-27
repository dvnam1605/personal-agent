import sys
import traceback

def try_import():
    try:
        # Try to import the retrieval module
        from app.services.retrieval import RetrievalQuery, DenseRetrievalService, SparseRetrievalService, HybridRetrievalService
        print("✅ Successfully imported retrieval services")
        # Try to instantiate the models
        query = RetrievalQuery(mode='CORPUS_SEARCH', query_text='test', limit=5)
        print(f"✅ Created RetrievalQuery: {query}")
        # We cannot instantiate the services without dependencies, but we can check that the classes exist
        print(f"✅ DenseRetrievalService class: {DenseRetrievalService}")
        print(f"✅ SparseRetrievalService class: {SparseRetrievalService}")
        print(f"✅ HybridRetrievalService class: {HybridRetrievalService}")
        return True
    except Exception as e:
        print(f"❌ Error importing retrieval services: {e}")
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = try_import()
    sys.exit(0 if success else 1)