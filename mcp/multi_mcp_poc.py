"""단일 파이썬 프로세스에서 path로 분리된 다중 MCP 서버 PoC (mcp 1.29).

구조:  /calc/mcp  → calc 서버 (add 도구)
       /text/mcp  → text 서버 (upper 도구)
클라이언트는 필요한 URL만 등록해 사용.
"""
import asyncio, contextlib
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount
from mcp.server.fastmcp import FastMCP
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PORT = 8765

# ── 서버 정의부: 프로젝트 내 모듈별로 두면 되는 부분 ─────────────────────────
calc = FastMCP("calc-server", stateless_http=True)
text = FastMCP("text-server", stateless_http=True)

@calc.tool()
def add(a: int, b: int) -> int:
    return a + b

@text.tool()
def upper(s: str) -> str:
    return s.upper()

REGISTRY = {"calc": calc, "text": text}   # 이름 → 서버 (마운트 경로가 URL이 됨)

# ── 조립부: 루트 ASGI 앱에 path별 mount + 세션매니저 lifespan 결합 ───────────
@contextlib.asynccontextmanager
async def lifespan(app):
    async with contextlib.AsyncExitStack() as stack:
        for srv in REGISTRY.values():
            await stack.enter_async_context(srv.session_manager.run())
        yield

app = Starlette(
    routes=[Mount(f"/{name}", app=srv.streamable_http_app())
            for name, srv in REGISTRY.items()],
    lifespan=lifespan,
)

# ── 검증: 서버 기동 → 각 URL에 별도 클라이언트로 접속 ────────────────────────
async def probe(url: str):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            return tools

async def main():
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.8)                       # 기동 대기

    for name in REGISTRY:
        url = f"http://127.0.0.1:{PORT}/{name}/mcp"
        tools = await probe(url)
        print(f"{url}  →  도구: {tools}")

    # 교차 확인: calc URL에는 text 도구가 없어야 함
    calc_tools = await probe(f"http://127.0.0.1:{PORT}/calc/mcp")
    print(f"격리 확인: calc 서버에 'upper' 없음 = {'upper' not in calc_tools}")

    # 실제 호출
    async with streamablehttp_client(f"http://127.0.0.1:{PORT}/calc/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("add", {"a": 2, "b": 40})
            print(f"calc.add(2,40) = {res.content[0].text}"
                  f" (structured={res.structuredContent})")

    server.should_exit = True
    await task

asyncio.run(main())
