"""레이어 분리 PoC: 정의 레이어(배포 무관) + 배포 레이어(환경변수로 A/B안 선택).

  MCP_SERVE_MODE=path  MCP_HTTP_PORT=8765            → A안 (단일 포트, path 분리)
  MCP_SERVE_MODE=port  MCP_PORT_MAP=calc=8766,text=8767 → B안 (포트 분리)
  MCP_SERVERS=calc                                    → 서브셋 (C안 겸용)
"""
import asyncio, contextlib, os
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

# ═══ 레이어 1: 서버 정의 (배포 지식 전무 — 실제로는 servers/ 패키지의 모듈들) ═══
from mcp.server.fastmcp import FastMCP

def build_calc() -> FastMCP:
    srv = FastMCP("calc-server", stateless_http=True)
    @srv.tool()
    def add(a: int, b: int) -> int:
        return a + b
    return srv

def build_text() -> FastMCP:
    srv = FastMCP("text-server", stateless_http=True)
    @srv.tool()
    def upper(s: str) -> str:
        return s.upper()
    return srv

REGISTRY = {"calc": build_calc, "text": build_text}   # 이름 → 팩토리 (lazy)

# ═══ 레이어 2: 배포 (환경변수가 유일한 제어면) ═══════════════════════════════
def load_config():
    names = [n for n in os.environ.get("MCP_SERVERS", "").split(",") if n] \
            or list(REGISTRY)
    mode = os.environ.get("MCP_SERVE_MODE", "path")
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    if mode == "path":
        return mode, host, {n: int(os.environ.get("MCP_HTTP_PORT", "8765"))
                            for n in names}
    pm = dict(kv.split("=") for kv in os.environ.get("MCP_PORT_MAP", "").split(",") if kv)
    base = int(os.environ.get("MCP_PORT_BASE", "8766"))
    return mode, host, {n: int(pm.get(n, base + i)) for i, n in enumerate(names)}

async def serve():
    mode, host, plan = load_config()
    instances = {n: REGISTRY[n]() for n in plan}          # 팩토리 실행은 배포 시점에

    if mode == "path":                                    # ── A안 조립
        @contextlib.asynccontextmanager
        async def lifespan(app):
            async with contextlib.AsyncExitStack() as st:
                for s in instances.values():
                    await st.enter_async_context(s.session_manager.run())
                yield
        app = Starlette(
            routes=[Route("/healthz", lambda r: JSONResponse({"ok": True}))] +
                   [Mount(f"/{n}", app=s.streamable_http_app())
                    for n, s in instances.items()],
            lifespan=lifespan)
        port = next(iter(plan.values()))
        servers = [uvicorn.Server(uvicorn.Config(app, host=host, port=port,
                                                 log_level="error"))]
        urls = {n: f"http://{host}:{port}/{n}/mcp" for n in instances}
    else:                                                 # ── B안 조립
        servers = [uvicorn.Server(uvicorn.Config(s.streamable_http_app(),
                   host=host, port=plan[n], log_level="error"))
                   for n, s in instances.items()]
        urls = {n: f"http://{host}:{plan[n]}/mcp" for n in instances}

    tasks = [asyncio.create_task(s.serve()) for s in servers]
    await asyncio.sleep(0.8)
    return servers, tasks, urls

# ═══ 검증 드라이버 ═══════════════════════════════════════════════════════════
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def probe(url):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return [t.name for t in (await s.list_tools()).tools]

async def main():
    servers, tasks, urls = await serve()
    mode = os.environ.get("MCP_SERVE_MODE", "path")
    for n, url in urls.items():
        print(f"[{mode}] {url}  →  {await probe(url)}")
    for s in servers: s.should_exit = True
    await asyncio.gather(*tasks)

asyncio.run(main())
