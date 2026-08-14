"""'매번 conn 정리'의 두 해석 비교 + timeout 계층 실측 (SQLAlchemy async).

A) 엔진(풀) 매번 생성·dispose  — "가장 간단"해 보이는 util
B) 엔진 캐시 + 커넥션만 대여   — DBHub 방식
"""
import asyncio, time
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DSN = "sqlite+aiosqlite:///./bench.db"
N = 30

async def setup():
    e = create_async_engine(DSN)
    async with e.begin() as c:
        await c.execute(text("CREATE TABLE IF NOT EXISTS t(x INT)"))
        await c.execute(text("DELETE FROM t"))
        await c.execute(text("INSERT INTO t VALUES (1)"))
    await e.dispose()

async def bench_a():                       # 매 호출 엔진 생성 → dispose
    t0 = time.perf_counter()
    for _ in range(N):
        eng = create_async_engine(DSN)
        async with eng.connect() as c:
            await c.execute(text("SELECT x FROM t"))
        await eng.dispose()
    return time.perf_counter() - t0

async def bench_b(eng):                    # 엔진 캐시 + 커넥션만 대여/반납
    t0 = time.perf_counter()
    for _ in range(N):
        async with eng.connect() as c:
            await c.execute(text("SELECT x FROM t"))
    return time.perf_counter() - t0

async def leak_check(eng):
    """예외가 나도 컨텍스트 매니저가 반납하는지 (정리 보장)."""
    try:
        async with eng.connect() as c:
            await c.execute(text("SELECT * FROM no_such_table"))
    except Exception:
        pass
    return eng.pool.checkedout()

async def timeout_layers(eng):
    """3계층: ① 풀 대기(pool_timeout) ② 문 실행 ③ 전체(asyncio)."""
    out = {}
    # ② 문 실행 타임아웃 — DB 벤더 기능(예: PG statement_timeout)이 정석.
    #    여기선 sqlite라 asyncio.timeout으로 상위 절단만 시연.
    try:
        async with asyncio.timeout(0.05):
            async with eng.connect() as c:
                await c.execute(text("SELECT 1"))
                await asyncio.sleep(0.2)          # 느린 쿼리 흉내
        out["overall_timeout"] = "미발동"
    except TimeoutError:
        out["overall_timeout"] = "발동(TimeoutError)"
    out["checked_out_after_timeout"] = eng.pool.checkedout()
    return out

async def main():
    await setup()
    ta = await bench_a()
    eng = create_async_engine(DSN, pool_pre_ping=True)
    tb = await bench_b(eng)
    print(f"A) 엔진 매번 생성·dispose : {ta*1000:7.1f} ms / {N}회")
    print(f"B) 엔진 캐시+커넥션 대여  : {tb*1000:7.1f} ms / {N}회   → B가 {ta/tb:.1f}배 빠름")
    print(f"예외 발생 후 반납 확인    : checked_out={await leak_check(eng)} (0=누수 없음)")
    print(f"타임아웃 계층            : {await timeout_layers(eng)}")
    await eng.dispose()

asyncio.run(main())
