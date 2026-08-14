"""MCP 조립 — 도구를 등록만 한다."""
from mcp.server.fastmcp import FastMCP
from . import tools

def build() -> FastMCP:
    srv = FastMCP("sales", stateless_http=True)
    srv.tool()(tools.top_orders)
    return srv
