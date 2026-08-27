import asyncpg
import asyncio

async def main():
    conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
    try:
        rows = await conn.fetch("""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'documents'
            ORDER BY ordinal_position;
        """)
        for row in rows:
            print(f"{row['column_name']}: {row['data_type']}, nullable={row['is_nullable']}, default={row['column_default']}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())