"""DB 커넥션 중앙화 PoC — DBHub 패턴 (mcp 1.29 + SQLAlchemy async).

검증 항목:
  ① 엔진 lazy 생성·프로세스 내 캐시 — 서버 여러 개가 같은 타깃이면 같은 엔진(풀 통합)
  ② 서버별 타깃 allowlist — 담당 아닌 데이터 접근 차단
  ③ 고수준 API의 가드레일 — row limit 강제·초과 감지
  ④ 커넥션 단기 대여 — 도구 호출 안에서만 점유, 호출 후 풀 반납
  ⑤ lifespan 종료 시 일괄 dispose
"""
import asyncio, contextlib
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

# ═══ 중앙 커넥션 레이어 (플랫폼 소유) ════════════════════════════════════════
class DBHub:
    def __init__(self, targets: dict[str, str],
                 access_plan: dict[str, set[str]] | None = None,
                 default_row_limit: int = 500):
        self._targets = targets                  # 타깃명 → DSN (실전: Secret/env)
        self._plan = access_plan or {}           # 서버명 → 허용 타깃 (없으면 전체 허용)
        self._row_limit = default_row_limit
        self._engines: dict[str, object] = {}    # 프로세스 내 타깃당 1엔진 (풀 통합)

    def engine(self, target: str):
        if target not in self._engines:          # lazy — 루프 안에서 최초 사용 시 생성
            self._engines[target] = create_async_engine(
                self._targets[target], pool_pre_ping=True)
        return self._engines[target]

    def _authorize(self, server: str, target: str):
        allowed = self._plan.get(server)
        if allowed is not None and target not in allowed:
            raise PermissionError(
                f"server '{server}' is not allowed to access target '{target}'")

    @contextlib.asynccontextmanager
    async def connect(self, target: str, *, server: str):
        """저수준: 단기 대여 — 도구 호출 스코프 안에서만 사용."""
        self._authorize(server, target)
        async with self.engine(target).connect() as conn:
            yield conn

    async def fetch_all(self, target: str, sql: str, params: dict | None = None,
                        *, server: str, row_limit: int | None = None) -> dict:
        """고수준(권장): row limit 가드레일 내장, dict 반환."""
        limit = row_limit or self._row_limit
        async with self.connect(target, server=server) as conn:
            res = await conn.execute(text(sql), params or {})
            rows = res.mappings().fetchmany(limit + 1)
            truncated = len(rows) > limit
            return {"rows": [dict(r) for r in rows[:limit]],
                    "row_count": min(len(rows), limit), "truncated": truncated}

    async def shutdown(self):
        for e in self._engines.values():
            await e.dispose()

# ═══ 배포 레이어 설정 (실전: env/Secret에서 조립) ════════════════════════════
hub = DBHub(
    targets={"sales": "sqlite+aiosqlite:///./poc_sales.db",
             "hr":    "sqlite+aiosqlite:///./poc_hr.db"},
    access_plan={"sales-mcp": {"sales"}, "hr-mcp": {"hr"}},   # 담당 데이터만
    default_row_limit=3,
)

# ═══ 정의 레이어: 담당자별 서버 — hub의 좁은 API만 사용 ══════════════════════
sales_srv = FastMCP("sales-mcp", stateless_http=True)
hr_srv = FastMCP("hr-mcp", stateless_http=True)

@sales_srv.tool()
async def top_orders(n: int = 5) -> dict:
    return await hub.fetch_all("sales", "SELECT * FROM orders ORDER BY amount DESC",
                               server="sales-mcp", row_limit=n)

@sales_srv.tool()
async def try_hr_access() -> dict:            # 월권 시도 (검증용)
    try:
        return await hub.fetch_all("hr", "SELECT * FROM employees", server="sales-mcp")
    except PermissionError as e:
        return {"error": str(e), "error_code": "TARGET_FORBIDDEN"}

@hr_srv.tool()
async def headcount() -> dict:
    return await hub.fetch_all("hr", "SELECT dept, COUNT(*) c FROM employees GROUP BY dept",
                               server="hr-mcp")

# ═══ 검증 ════════════════════════════════════════════════════════════════════
async def seed():
    e1 = create_async_engine("sqlite+aiosqlite:///./poc_sales.db")
    async with e1.begin() as c:
        await c.execute(text("CREATE TABLE IF NOT EXISTS orders(id INT, amount INT)"))
        await c.execute(text("DELETE FROM orders"))
        for i, amt in enumerate([100, 900, 300, 700, 500]):
            await c.execute(text("INSERT INTO orders VALUES (:i,:a)"), {"i": i, "a": amt})
    await e1.dispose()
    e2 = create_async_engine("sqlite+aiosqlite:///./poc_hr.db")
    async with e2.begin() as c:
        await c.execute(text("CREATE TABLE IF NOT EXISTS employees(name TEXT, dept TEXT)"))
        await c.execute(text("DELETE FROM employees"))
        for n, d in [("kim","eng"),("lee","eng"),("park","ops")]:
            await c.execute(text("INSERT INTO employees VALUES (:n,:d)"), {"n": n, "d": d})
    await e2.dispose()

async def call(srv, tool, args=None):
    import json
    async with create_connected_server_and_client_session(srv._mcp_server) as s:
        r = await s.call_tool(tool, args or {})
        # 실측 문서의 함정 그대로: 제네릭 dict 반환은 structuredContent가 없다 → text JSON 파싱
        return r.structuredContent or json.loads(r.content[0].text)

async def main():
    await seed()
    r = await call(sales_srv, "top_orders", {"n": 2})
    print(f"① sales.top_orders(2) → rows={r['rows']} truncated={r['truncated']}")
    eng_a = id(hub.engine("sales"))
    r = await call(sales_srv, "top_orders", {"n": 5})
    print(f"③ row_limit 가드레일: n=5 요청 → count={r['row_count']} truncated={r['truncated']} (limit 초과 감지)")
    eng_b = id(hub.engine("sales"))
    print(f"② 엔진 캐시(풀 통합): 호출 간 동일 엔진 = {eng_a == eng_b}")
    r = await call(sales_srv, "try_hr_access")
    print(f"②' allowlist: sales-mcp의 hr 접근 → {r.get('error_code')} ({r.get('error','')[:60]})")
    r = await call(hr_srv, "headcount")
    print(f"   hr.headcount → {r['rows']}")
    pool = hub.engine("sales").pool
    print(f"④ 호출 후 풀 상태: checked_out={pool.checkedout()} (0=반납 완료)")
    await hub.shutdown()
    print("⑤ hub.shutdown() → 전체 엔진 dispose 완료")

asyncio.run(main())
