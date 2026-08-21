import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect('postgresql://postgres:postgres@localhost:5432/postgres')
    await conn.execute("DROP TABLE IF EXISTS t CASCADE; CREATE TABLE t (status VARCHAR(20));")
    res = await conn.fetch("EXPLAIN SELECT * FROM t WHERE status IN ('a', 'b')")
    for r in res:
        print(r[0])
    await conn.close()
asyncio.run(main())
