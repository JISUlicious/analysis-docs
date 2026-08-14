from mcp.server.fastmcp import FastMCP
from . import tools
def build() -> FastMCP:
    srv = FastMCP("hr", stateless_http=True)
    srv.tool()(tools.headcount)
    return srv
