"""도구 수 증가의 실제 비용 + 관리 기법 검증 (SDK 1.29)."""
import asyncio, json
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

DESC = ("Retrieve records filtered by the given criteria. Use this when the user asks "
        "about {d} data and needs concrete rows rather than a summary.")

def make_server(n, domains=("sales","hr","finance","ops","support")):
    srv = FastMCP("many")
    for i in range(n):
        d = domains[i % len(domains)]
        def make(dom):
            def f(query: str, limit: int = 10, since: str = "") -> dict:
                return {"domain": dom}
            return f
        f = make(d)
        f.__name__ = f"{d}_query_{i}"
        f.__doc__ = DESC.format(d=d)
        srv.tool()(f)
    return srv

async def measure(n):
    srv = make_server(n)
    async with create_connected_server_and_client_session(srv._mcp_server) as s:
        tools = (await s.list_tools()).tools
        payload = json.dumps([t.model_dump(exclude_none=True) for t in tools])
        return len(tools), len(payload)

async def main():
    print("═══ ① 도구 수 → tools/list 선언 크기 (매 LLM 스텝 프롬프트에 포함) ═══")
    base = None
    for n in (5, 20, 50, 100):
        cnt, size = await measure(n)
        tok = size / 3.5                      # 대략 추정
        if base is None: base = size / n
        print(f"  {cnt:3d}개 → {size:7,d}자 (~{tok:6,.0f} 토큰)  도구당 평균 {size/n:5.0f}자")

    print("\n═══ ② 런타임 도구 추가/제거 지원 여부 ═══")
    srv = make_server(3)
    async with create_connected_server_and_client_session(srv._mcp_server) as s:
        before = len((await s.list_tools()).tools)
        def extra(x: int) -> int:
            "Added at runtime."
            return x
        srv.tool()(extra)                       # 런타임 추가
        after = len((await s.list_tools()).tools)
        print(f"  추가 전 {before} → 추가 후 {after}  (동적 변경 {'가능' if after > before else '불가'})")
        rm = getattr(srv, "remove_tool", None)
        print(f"  remove_tool API: {'있음' if rm else '없음 (1.29)'}")

    print("\n═══ ③ 라우터(메타도구) 패턴 — 도구 2개로 N개 능력 노출 ═══")
    router = FastMCP("router")
    CAPS = {f"{d}_query": DESC.format(d=d) for d in ("sales","hr","finance","ops","support")}
    @router.tool()
    def list_capabilities(domain: str = "") -> dict:
        """List available operations. Call this first to discover what you can do."""
        return {"capabilities": [{"name": k, "description": v} for k, v in CAPS.items()
                                 if not domain or k.startswith(domain)]}
    @router.tool()
    def invoke(capability: str, args: dict) -> dict:
        """Invoke a capability discovered via list_capabilities."""
        return {"ok": True, "capability": capability}
    async with create_connected_server_and_client_session(router._mcp_server) as s:
        tools = (await s.list_tools()).tools
        size = len(json.dumps([t.model_dump(exclude_none=True) for t in tools]))
        print(f"  노출 도구 {len(tools)}개 → 선언 {size:,}자 (~{size/3.5:.0f} 토큰)")
        r = await s.call_tool("list_capabilities", {"domain": "hr"})
        print(f"  discover 호출 결과: {r.content[0].text[:80]}...")

asyncio.run(main())
