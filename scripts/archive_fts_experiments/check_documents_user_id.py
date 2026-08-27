import asyncpg
import asyncio

async def main():
    conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
    try:
        # Check if user_id column is nullable
        row = await conn.fetchrow("""
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_name = 'documents' AND column_name = 'user_id';
        """)
        if row:
            print(f"user_id column is nullable: {row['is_nullable']}")
        else:
            print("Column user_id not found in documents table.")
        # Also check if there is a provenance_uri column
        row2 = await conn.fetchrow("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'documents' AND column_name = 'provenance_uri';
        """)
        if row2:
            print(f"provenance_uri column exists: {row2['data_type']}")
        else:
            print("provenance_uri column does not exist in documents table.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())