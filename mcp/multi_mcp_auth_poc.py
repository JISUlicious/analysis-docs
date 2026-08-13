"""인증 미들웨어 PoC: 서버(mount)별 Bearer 토큰 — MCP_AUTH_TOKENS로 제어.
   검증: ①무토큰→401 ②타 서버 토큰→401 ③정상 토큰→도구 목록 ④/healthz는 무인증.
"""
import asyncio, contextlib, os
import httpx, uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from mcp.server.fastmcp import FastMCP
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# ── 레이어 1: 정의 ──────────────────────────────────────────────────────────
def build_calc():
    srv = FastMCP("calc-server", stateless_http=True)
    @srv.tool()
    def add(a: int, b: int) -> int: return a + b
    return srv

def build_text():
    srv = FastMCP("text-server", stateless_http=True)
    @srv.tool()
    def upper(s: str) -> str: return s.upper()
    return srv

REGISTRY = {"calc": build_calc, "text": build_text}

# ── 인증 미들웨어 (순수 ASGI 래퍼 — mount 단위 적용) ─────────────────────────
class BearerAuth:
    def __init__(self, app, token: str):
        self.app, self.expected = app, f"Bearer {token}"
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            hdrs = dict(scope.get("headers") or [])
            if hdrs.get(b"authorization", b"").decode() != self.expected:
                resp = JSONResponse({"error": "unauthorized"}, status_code=401)
                return await resp(scope, receive, send)
        return await self.app(scope, receive, send)

# ── 레이어 2: 배포 (path 모드 + 서버별 토큰) ─────────────────────────────────
PORT = 8765
TOKENS = dict(kv.split("=") for kv in
              os.environ.get("MCP_AUTH_TOKENS", "calc=tok-calc,text=tok-text").split(","))

async def main():
    instances = {n: f() for n, f in REGISTRY.items()}

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with contextlib.AsyncExitStack() as st:
            for s in instances.values():
                await st.enter_async_context(s.session_manager.run())
            yield

    app = Starlette(
        routes=[Route("/healthz", lambda r: JSONResponse({"ok": True}))] +
               [Mount(f"/{n}", app=BearerAuth(s.streamable_http_app(), TOKENS[n]))
                for n, s in instances.items()],
        lifespan=lifespan)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT,
                                           log_level="error"))
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.8)
    base = f"http://127.0.0.1:{PORT}"

    async with httpx.AsyncClient() as hc:
        r = await hc.post(f"{base}/calc/mcp", json={})
        print(f"① 무토큰            → HTTP {r.status_code}")
        r = await hc.post(f"{base}/calc/mcp", json={},
                          headers={"Authorization": "Bearer tok-text"})
        print(f"② calc에 text 토큰  → HTTP {r.status_code}")
        r = await hc.get(f"{base}/healthz")
        print(f"④ /healthz 무인증   → HTTP {r.status_code} {r.json()}")

    async with streamablehttp_client(f"{base}/calc/mcp",
            headers={"Authorization": "Bearer tok-calc"}) as (rd, wr, _):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            res = await s.call_tool("add", {"a": 1, "b": 2})
            print(f"③ 정상 토큰(calc)   → 도구 {tools}, add(1,2)={res.content[0].text}")

    server.should_exit = True
    await task

asyncio.run(main())
