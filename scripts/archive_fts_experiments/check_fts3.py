import asyncpg
import asyncio

async def test():
    try:
        conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
        print('Connected to PostgreSQL')
        # Check if search_vector column exists
        query = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'document_chunks' AND column_name = 'search_vector';
        """
        result = await conn.fetch(query)
        if result:
            print(f"Column search_vector exists: {result[0]}")
            # Check index
            index_query = """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'document_chunks' AND indexname = 'idx_document_chunks_search_vector';
            """
            index_result = await conn.fetch(index_query)
            if index_result:
                print(f"Index exists: {index_result[0]}")
            else:
                print("Index does not exist.")
        else:
            print("Column search_vector does NOT exist.")
            print("You need to apply migration 0007 to add tsvector column and GIN index.")
        await conn.close()
    except Exception as e:
        print(f'Error: {e}')

asyncio.run(test())