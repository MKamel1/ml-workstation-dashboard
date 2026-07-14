"""Standalone check that the MCP server actually works over the real MCP
stdio protocol (not just as plain Python function calls) -- spawns
server.py as a subprocess, the same way Claude Code/Desktop would, and
exercises every tool against the live dashboard.

Requires the dashboard to be running (systemctl --user status ml-dashboard).
Plain asserts, no test framework. Run directly: venv/bin/python test_server.py
"""

import asyncio
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(SERVER_DIR, "venv", "bin", "python")
SERVER_SCRIPT = os.path.join(SERVER_DIR, "server.py")

EXPECTED_TOOLS = {
    "get_current_metrics", "get_dashboard_config", "get_db_stats", "get_history",
    "export_history", "get_lighting_state", "get_lighting_modes", "set_lighting",
}


async def main():
    params = StdioServerParameters(command=PYTHON, args=[SERVER_SCRIPT])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}
            assert tool_names == EXPECTED_TOOLS, (
                f"tool set mismatch: expected {EXPECTED_TOOLS}, got {tool_names}"
            )

            # A read-only tool against real data.
            result = await session.call_tool("get_db_stats", {})
            assert not result.isError, f"get_db_stats failed: {result.content}"
            assert '"total_records"' in result.content[0].text

            # A parameterized read-only tool.
            result = await session.call_tool("get_history", {"limit": 3})
            assert not result.isError, f"get_history failed: {result.content}"
            assert '"count"' in result.content[0].text

            # The one write tool -- round-trip on/off so this test doesn't
            # leave the lights in a different state than it found them.
            result = await session.call_tool(
                "set_lighting", {"power": "on", "mode": "direct", "color": "#123456", "brightness": 50}
            )
            assert not result.isError, f"set_lighting(on) failed: {result.content}"
            assert '"power": "on"' in result.content[0].text or '"power":"on"' in result.content[0].text

            result = await session.call_tool("set_lighting", {"power": "off"})
            assert not result.isError, f"set_lighting(off) failed: {result.content}"

            # Invalid input must come back as a tool error, not crash the server.
            result = await session.call_tool("set_lighting", {"power": "blink"})
            assert result.isError, "set_lighting('blink') should have been rejected"

    print("mcp_server/test_server.py passed")


if __name__ == "__main__":
    asyncio.run(main())
