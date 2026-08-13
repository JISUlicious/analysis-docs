"""B안 PoC: 단일 프로세스 · 동일 host · 포트별 MCP 서버 (mcp 1.29).
   :8766/mcp → calc,  :8767/mcp → text
"""
import asyncio
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

calc = FastMCP("calc-server", stateless_http=True)
text = FastMCP("text-server", stateless_http=True)

@calc.tool()
def add(a: int, b: int) -> int:
    return a + b

@text.tool()
def upper(s: str) -> str:
    return s.upper()

PORTS = {8766: calc, 8767: text}

async def probe(url):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return [t.name for t in (await s.list_tools()).tools]

async def main():
    servers, tasks = [], []
    for port, srv in PORTS.items():
        # 직접 서빙: 앱 자체 lifespan이 session_manager를 구동하는지 검증 포인트
        cfg = uvicorn.Config(srv.streamable_http_app(), host="127.0.0.1",
                             port=port, log_level="error")
        us = uvicorn.Server(cfg)
        servers.append(us)
        tasks.append(asyncio.create_task(us.serve()))
    await asyncio.sleep(0.8)

    for port, srv in PORTS.items():
        url = f"http://127.0.0.1:{port}/mcp"
        print(f"{url}  →  도구: {await probe(url)}")

    async with streamablehttp_client("http://127.0.0.1:8767/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("upper", {"s": "port-based"})
            print(f"text.upper('port-based') = {res.content[0].text}")

    for us in servers: us.should_exit = True
    await asyncio.gather(*tasks)

asyncio.run(main())
