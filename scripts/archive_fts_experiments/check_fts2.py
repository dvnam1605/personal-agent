import asyncpg
import asyncio
import sys

print("Starting check_fts2", flush=True)

async def check_fts():
    try:
        print("Attempting connection...", flush=True)
        conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
        print('Connected to PostgreSQL', flush=True)
        # Check if search_vector column exists
        query = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'document_chunks' AND column_name = 'search_vector';
        """
        result = await conn.fetch(query)
        if result:
            print(f"Column search_vector exists: {result[0]}", flush=True)
            # Check index
            index_query = """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'document_chunks' AND indexname = 'idx_document_chunks_search_vector';
            """
            index_result = await conn.fetch(index_query)
            if index_result:
                print(f"Index exists: {index_result[0]}", flush=True)
            else:
                print("Index does not exist.", flush=True)
        else:
            print("Column search_vector does NOT exist.", flush=True)
            print("You need to apply migration 0007 to add tsvector column and GIN index.", flush=True)
        # Optionally, test a simple search if column exists
        if result:
            test_query = """
            SELECT COUNT(*)
            FROM document_chunks
            WHERE search_vector @@ to_tsquery('simple', 'test');
            """
            count = await conn.fetchval(test_query)
            print(f"Test search for 'test' returned {count} rows.", flush=True)
        await conn.close()
    except Exception as e:
        print(f"Error: {e}", flush=True)

asyncio.run(check_fts())