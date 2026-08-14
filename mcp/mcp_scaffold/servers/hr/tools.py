from mcp_platform.db import hub
async def headcount() -> dict:
    return await hub.fetch_all("hr", "SELECT dept, COUNT(*)", server="hr")
