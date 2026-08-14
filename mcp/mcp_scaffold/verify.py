"""스캐폴드 검증: 발견·격리·서브셋·의존방향·도구 단위테스트."""
import ast, asyncio, pathlib, sys
from mcp_platform.registry import discover

def check_dependency_direction(root="servers"):
    """규칙: servers/<A>는 servers/<B>(A≠B)를 import 금지."""
    violations = []
    for py in pathlib.Path(root).rglob("*.py"):
        owner = py.relative_to(root).parts[0]
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            for m in mods:
                parts = m.split(".")
                if parts[0] == "servers" and len(parts) > 1 and parts[1] != owner:
                    violations.append(f"{py}: imports {m}")
    return violations

async def main():
    print("① 전체 발견 (실패 격리 확인)")
    found, failed = discover()
    print(f"   성공: {sorted(found)}")
    print(f"   실패: { {k: v[:40] for k, v in failed.items()} }")
    print(f"   → broken 하나가 죽어도 나머지 {len(found)}개 정상 기동 = {len(found) == 2}")

    print("② 서브셋 선택 (MCP_SERVERS=sales 상당)")
    found2, _ = discover(only={"sales"})
    print(f"   → {sorted(found2)}")

    print("③ build() 규약 준수 + 도구 노출")
    srv = found["sales"]()
    tools = [t.name for t in await srv.list_tools()]
    print(f"   sales 서버 도구: {tools}")

    print("④ 의존 방향 검사 (servers 간 교차 import 금지)")
    v = check_dependency_direction()
    print(f"   위반: {v if v else '없음 ✅'}")
    # 고의 위반 주입 후 재검사
    p = pathlib.Path("servers/hr/leak.py"); p.write_text("from servers.sales import tools\n")
    v2 = check_dependency_direction(); p.unlink()
    print(f"   (고의 위반 주입 시) 검출: {v2}")

    print("⑤ 도구 단위 테스트 — MCP 없이 순수 함수로")
    from servers.sales import tools as sales_tools
    r = await sales_tools.top_orders(3)
    print(f"   top_orders(3) 직접 호출 → {r['rows']}")

    print("⑥ DBHub allowlist가 디렉터리 경계와 일치")
    from servers.hr import tools as hr_tools
    from mcp_platform.db import hub
    try:
        await hub.fetch_all("sales", "SELECT 1", server="hr")   # hr이 sales 접근
        print("   ❌ 차단 실패")
    except PermissionError as e:
        print(f"   ✅ 차단됨: {e}")

asyncio.run(main())
