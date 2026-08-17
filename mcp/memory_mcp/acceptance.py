import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def run():
    async with streamablehttp_client("http://mcp:8787/mcp") as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            expected = {
                "memory_search",
                "memory_get",
                "memory_recent",
                "memory_get_session_summary",
                "memory_find_procedures",
                "memory_find_decisions",
                "memory_find_preferences",
                "memory_find_similar_errors",
            }
            if not expected.issubset(names):
                raise RuntimeError("MCP tools are incomplete: %s" % sorted(names))
            result = await session.call_tool(
                "memory_search",
                {
                    "query": "memoria externa para OpenCode",
                    "project": "comercial-j11",
                    "limit": 3,
                },
            )
            rendered = "\n".join(getattr(item, "text", "") for item in result.content)
            if "memoria externa" not in rendered:
                raise RuntimeError("Known memory was not retrieved through MCP")
            if os.getenv("MCP_ACCEPTANCE_SMOKE") == "1":
                procedure_result = await session.call_tool(
                    "memory_find_procedures",
                    {
                        "query": "como corregir facturas duplicadas",
                        "project": "smoke-project",
                        "limit": 3,
                        "verified_only": True,
                    },
                )
                procedure_text = "\n".join(
                    getattr(item, "text", "") for item in procedure_result.content
                )
                if "clave idempotente" not in procedure_text:
                    raise RuntimeError("Verified procedure was not retrieved through MCP")
            print("MCP search acceptance passed.")


if __name__ == "__main__":
    asyncio.run(run())
