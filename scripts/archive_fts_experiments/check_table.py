import asyncpg
import asyncio
import sys

async def main():
    try:
        conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
        print("Connected to PostgreSQL")
        # Check table exists
        query = """
        SELECT EXISTS (
            SELECT FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name = 'document_chunks'
        );
        """
        result = await conn.fetchval(query)
        print(f"Table document_chunks exists: {result}")
        if result:
            # Get columns
            cols_query = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'document_chunks'
            ORDER BY ordinal_position;
            """
            cols = await conn.fetch(cols_query)
            print("Columns:")
            for col in cols:
                print(f"  {col['column_name']}: {col['data_type']}")
            # Count rows
            count = await conn.fetchval("SELECT COUNT(*) FROM document_chunks;")
            print(f"Row count: {count}")
            # If there are rows, show a sample
            if count > 0:
                sample = await conn.fetch("SELECT * FROM document_chunks LIMIT 1;")
                print("Sample row (first):")
                for key in sample[0].keys():
                    print(f"  {key}: {sample[0][key]}")
        await conn.close()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Script finished")

asyncio.run(main())