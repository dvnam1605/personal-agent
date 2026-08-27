import asyncpg
import asyncio

async def main():
    conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
    try:
        row = await conn.fetchrow("SELECT version_num FROM alembic_version;")
        print(f"Current alembic version: {row['version_num'] if row else 'None'}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())