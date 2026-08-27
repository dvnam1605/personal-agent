import asyncpg
import asyncio

async def test():
    try:
        conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
        print('Connected to PostgreSQL')
        # Test a simple query
        result = await conn.fetchval('SELECT version()')
        print(f'PostgreSQL version: {result}')
        await conn.close()
    except Exception as e:
        print(f'Error: {e}')

asyncio.run(test())