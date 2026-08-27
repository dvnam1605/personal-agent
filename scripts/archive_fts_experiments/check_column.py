import asyncpg
import asyncio
import sys

async def main():
    try:
        conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
        print("Connected to PostgreSQL")
        sys.stdout.flush()
        query = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'document_chunks' AND column_name = 'search_vector';
        """
        result = await conn.fetch(query)
        if result:
            print(f"Column exists: {result[0]}")
        else:
            print("Column does NOT exist.")
        sys.stdout.flush()
        await conn.close()
    except Exception as e:
        print(f"Error: {e}")
        sys.stdout.flush()

asyncio.run(main())
print("Script finished")
sys.stdout.flush()