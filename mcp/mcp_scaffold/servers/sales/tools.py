"""도구 구현 — 순수 함수. MCP/전송 지식 없음 → 단위 테스트 쉬움."""
from mcp_platform.db import hub

async def top_orders(n: int = 5) -> dict:
    return await hub.fetch_all("sales", "SELECT * FROM orders", server="sales", row_limit=n)
